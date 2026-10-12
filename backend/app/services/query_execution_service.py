"""Query execution service for Query Studio (QS-T1/T2/T3/T5).

Executes user-authored SQL against a connection:
  - classifies the statement (statement_classifier) to detect read vs
    write/DDL, and rejects multi-statement input outright — most DB-API
    drivers don't support more than one statement per execute() call, and
    for a write/DDL statement specifically, ambiguity about what actually
    ran is a real risk (stacked-statement injection is the classic form of
    this), so single-statement-only is the safe default here.
  - SELECT executes via the connector's existing execute_query() path,
    paginated in-memory and capped at settings.QUERY_STUDIO_MAX_RESULT_ROWS.
    The connector layer has no server-side cursor/streaming support (every
    driver's execute_query() does a plain fetchall()), so this is a real,
    documented limitation, not an oversight — see query_studio_tasks/INDEX.md.
  - INSERT/UPDATE/DELETE/DDL require the admin role AND an explicit
    confirm=True; without confirm (or without the role) the endpoint
    classifies the statement and returns requires_confirmation=True without
    touching the database. Writes execute via a dedicated cursor + explicit
    commit — execute_query() never commits (fine for SELECTs; NL2SQL only
    ever ran those), so reusing it for a write would silently roll back on
    connection close.
  - a bounded timeout wraps execution via a worker thread.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Dict, List

from app.core.config import settings
from app.models.connection import DBConnection
from app.services.schema_service import get_connector
from app.services.statement_classifier import StatementType, classify

logger = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="query-studio-exec")

WRITE_TYPES = {StatementType.INSERT, StatementType.UPDATE, StatementType.DELETE, StatementType.DDL}


class QueryExecutionError(Exception):
    pass


def _run_with_timeout(func, timeout_seconds: int):
    future = _EXECUTOR.submit(func)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        raise QueryExecutionError(f"Query timed out after {timeout_seconds}s")


def _execute_read(connection: DBConnection, sql: str) -> List[Dict[str, Any]]:
    connector = get_connector(connection)
    try:
        if hasattr(connector, "execute_query"):
            return connector.execute_query(sql)
        conn = connector.connect()
        cur = conn.cursor()
        cur.execute(sql)
        if cur.description:
            keys = [d[0] for d in cur.description]
            return [dict(zip(keys, row)) for row in cur.fetchall()]
        return []
    finally:
        connector.close()


def _execute_write(connection: DBConnection, sql: str) -> int:
    connector = get_connector(connection)
    conn = None
    try:
        conn = connector.connect()
        cur = conn.cursor()
        cur.execute(sql)
        affected = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
        return affected
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("Rollback failed after write error", exc_info=True)
        raise
    finally:
        connector.close()


def execute(
    connection: DBConnection,
    sql: str,
    role: str,
    page: int,
    page_size: int,
    confirm: bool,
) -> Dict[str, Any]:
    """Classify + (maybe) execute *sql*. Returns a dict matching QueryExecuteResponse."""
    classified = classify(sql)
    result: Dict[str, Any] = {
        "statement_type": classified.type.value,
        "tables_referenced": classified.tables_referenced,
        "warnings": list(classified.warnings),
        "page": page,
        "page_size": page_size,
    }

    if classified.is_multi_statement:
        result["error"] = "Only one statement at a time is supported — remove the extra statement(s)."
        return result

    if classified.type in WRITE_TYPES:
        if role != "admin":
            result["requires_confirmation"] = True
            result["warnings"].append(f"{classified.type.value.upper()} statements require the admin role.")
            return result
        if not confirm:
            result["requires_confirmation"] = True
            result["warnings"].append(
                f"This is a {classified.type.value.upper()} statement — pass confirm=true to execute it."
            )
            return result

        start = time.monotonic()
        try:
            affected = _run_with_timeout(
                lambda: _execute_write(connection, sql),
                settings.QUERY_STUDIO_EXECUTION_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            result["error"] = str(exc)
            return result
        result["executed"] = True
        result["affected_rows"] = affected
        result["duration_ms"] = int((time.monotonic() - start) * 1000)
        return result

    if classified.type == StatementType.UNKNOWN:
        result["error"] = "Could not classify this statement as SELECT/write/DDL — refusing to execute."
        return result

    # SELECT
    start = time.monotonic()
    try:
        rows = _run_with_timeout(
            lambda: _execute_read(connection, sql),
            settings.QUERY_STUDIO_EXECUTION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        result["error"] = str(exc)
        return result
    duration_ms = int((time.monotonic() - start) * 1000)

    total = len(rows)
    truncated = total > settings.QUERY_STUDIO_MAX_RESULT_ROWS
    if truncated:
        rows = rows[: settings.QUERY_STUDIO_MAX_RESULT_ROWS]
        total = len(rows)
        result["warnings"].append(f"Result set truncated to {settings.QUERY_STUDIO_MAX_RESULT_ROWS} rows.")

    offset = (page - 1) * page_size
    page_rows = rows[offset: offset + page_size]

    result["executed"] = True
    result["columns"] = list((page_rows[0] if page_rows else (rows[0] if rows else {})).keys())
    result["rows"] = page_rows
    result["row_count"] = total
    result["has_more"] = (offset + page_size) < total
    result["truncated"] = truncated
    result["duration_ms"] = duration_ms
    return result
