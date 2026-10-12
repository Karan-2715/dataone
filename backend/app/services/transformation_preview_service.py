"""Transformation Preview (Enterprise v2, E10-7 — Schema Mapping Workbench).

Shows "source value -> transformation -> output" on a few real sample
rows, per the v3 workbench spec. Deliberately does NOT reuse
transformation_grammar.compile_sql's SQL path: that path emits
"%s"-style positional placeholders that pipeline_executor.py's own
docstring says are "designed for single-shot query preview, not
per-row batch ETL binding" — and no execution path in this codebase
(query_execution_service._execute_read) actually binds parameters; it
only runs a raw SQL string. Building a bespoke parameter binder for this
one feature would duplicate a security-sensitive concern. Instead: reuse
transformation_grammar.parse() for validation (single source of truth
on what's a legal transformation), then evaluate the SAME parsed
kind/payload in pure Python against a small, read-only sample fetched
via already-quoted identifiers — no SQL injection surface, no
parameter-binding gap.

The sample query runs in a worker thread with a hard timeout
(settings.CONNECTOR_TEST_TIMEOUT_SECONDS — the same 5s discipline
schema_service.test_connection and query_execution_service already use)
so a slow or unresponsive source connector degrades to an honest
"unavailable" instead of hanging the request (build-validation B-E10-12).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.connection import DBConnection
from app.models.mapping import FieldMapping, Mapping
from app.services.mapping_service import MappingService
from app.services.schema_service import get_connector
from app.services.transformation_grammar import GrammarError, parse

logger = logging.getLogger(__name__)

PREVIEW_ROW_LIMIT = 5


def _quote_identifier(name: str) -> str:
    """Mirrors the SQLite connector's own identifier-quoting fix
    (connector_tasks bugs #03) — no raw interpolation of a bare name."""
    return '"' + name.replace('"', '""') + '"'


def apply_transformation_preview(ast: dict[str, Any], source_values: dict[str, Any]) -> Any:
    """Pure function: apply one already-parsed transformation AST to one
    row's sampled source values. Mirrors transformation_grammar's SQL
    kinds exactly so the preview matches what publish-time execution
    does. Takes the parsed AST, not the raw transformation dict — the
    caller parses once and reuses it across every sample row rather than
    re-parsing per row (build-validation B-E10-13)."""
    kind = ast["kind"]
    payload = ast["payload"]
    values = list(source_values.values())
    first = values[0] if values else None

    if kind == "direct":
        return first
    if kind == "cast":
        target = (payload.get("to") or "").upper()
        if first is None:
            return None
        try:
            if "INT" in target:
                return int(first)
            if any(t in target for t in ("FLOAT", "REAL", "DOUBLE", "DECIMAL", "NUMERIC")):
                return float(first)
            return str(first)
        except (TypeError, ValueError):
            return None  # honest: this sample value doesn't cast cleanly
    if kind == "concat":
        parts = payload.get("parts", [])
        src_iter = iter(values)
        out = []
        for part in parts:
            if part.get("kind") == "literal":
                out.append(str(part.get("value", "")))
            else:
                out.append(str(next(src_iter, "") or ""))
        return "".join(out)
    if kind == "substring":
        text = str(first) if first is not None else ""
        start = int(payload.get("start", 0))
        length = int(payload.get("length", 0))
        return text[start:start + length]
    if kind == "coalesce":
        return first if first is not None else payload.get("fallback_value")
    if kind == "upper":
        return str(first).upper() if first is not None else None
    if kind == "lower":
        return str(first).lower() if first is not None else None
    if kind == "trim":
        return str(first).strip() if first is not None else None
    if kind == "default":
        return first if first is not None else payload.get("value")
    if kind == "null_if":
        return None if first == payload.get("equals") else first
    if kind == "lookup":
        return None  # requires a live join against the lookup table — not previewable client-side
    if kind == "case":
        # NULL is never "true" in a SQL comparison — CASE WHEN NULL > x THEN
        # ... ELSE ... END takes the ELSE branch. Returning else_value here
        # (not None) keeps the preview honest about what real execution
        # (_sql_case's CASE WHEN, and pipeline_executor's Python mirror of
        # it) will actually produce for a null source value.
        else_value = payload.get("else_value")
        if first is None:
            return else_value
        compare_value = payload.get("compare_value")
        op = payload.get("operator")
        try:
            if op == ">":
                matched = first > compare_value
            elif op == ">=":
                matched = first >= compare_value
            elif op == "<":
                matched = first < compare_value
            elif op == "<=":
                matched = first <= compare_value
            elif op == "==":
                matched = first == compare_value
            elif op == "!=":
                matched = first != compare_value
            else:
                return None  # unreachable once grammar-validated
        except TypeError:
            # e.g. comparing a string source value to a numeric threshold.
            return None  # honest: this sample value isn't comparable to compare_value
        return payload.get("then_value") if matched else else_value
    return None


class TransformationPreviewService:
    @staticmethod
    def get_preview(db: Session, mapping_id: int, edge_id: int) -> dict[str, Any]:
        mapping: Mapping = MappingService.get_mapping(db, mapping_id)
        edge = (
            db.query(FieldMapping)
            .filter(FieldMapping.id == edge_id, FieldMapping.mapping_id == mapping_id)
            .first()
        )
        if edge is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="edge not found")

        transformation = edge.transformation or {"kind": "direct"}
        try:
            ast = parse(transformation)
        except GrammarError as exc:
            return {"available": False, "reason": f"Invalid transformation: {exc}", "rows": []}

        if transformation.get("kind") == "lookup":
            return {
                "available": False,
                "reason": "Lookup transformations require a live join and cannot be previewed on sample data.",
                "rows": [],
            }

        sources = edge.sources or []
        if not sources:
            return {"available": False, "reason": "This edge has no source columns.", "rows": []}
        table = sources[0].get("table")
        if not table or not all(s.get("table") == table for s in sources):
            return {
                "available": False,
                "reason": "Preview only supports transformations reading from a single source table.",
                "rows": [],
            }
        columns = [s["column"] for s in sources if s.get("column")]
        if not columns:
            return {"available": False, "reason": "This edge has no source columns.", "rows": []}

        source_conn = db.query(DBConnection).filter(DBConnection.id == mapping.source_id).first()
        if source_conn is None:
            return {"available": False, "reason": "Source connection not found.", "rows": []}

        select_list = ", ".join(_quote_identifier(c) for c in columns)
        sql = f"SELECT {select_list} FROM {_quote_identifier(table)} LIMIT {PREVIEW_ROW_LIMIT}"

        connector = get_connector(source_conn)

        def _run_query() -> list[dict[str, Any]]:
            # connector.close() lives HERE, inside the worker thread's own
            # call, not in the outer function's finally — on a timeout the
            # outer call returns while this thread may still be blocked
            # inside execute()/fetchall(); closing the connector from the
            # main thread concurrently with that in-flight call would race.
            # Same accepted tradeoff as schema_service.test_connection and
            # query_execution_service._execute_read (build-validation
            # B-E10-12): a hard-hung driver call leaks until it eventually
            # returns, but the API request itself never hangs.
            try:
                if hasattr(connector, "execute_query"):
                    return connector.execute_query(sql)
                conn = connector.connect()
                cur = conn.cursor()
                cur.execute(sql)
                keys = [d[0] for d in cur.description] if cur.description else []
                return [dict(zip(keys, r)) for r in cur.fetchall()]
            finally:
                connector.close()

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            rows = executor.submit(_run_query).result(timeout=settings.CONNECTOR_TEST_TIMEOUT_SECONDS)
        except FutureTimeoutError:
            logger.warning(
                "[transformation_preview] mapping_id=%s edge_id=%s query timed out after %ss",
                mapping_id, edge_id, settings.CONNECTOR_TEST_TIMEOUT_SECONDS,
            )
            return {
                "available": False,
                "reason": f"Preview query timed out after {settings.CONNECTOR_TEST_TIMEOUT_SECONDS} seconds.",
                "rows": [],
            }
        except Exception as exc:  # noqa: BLE001 — a live connector failure is an honest "unavailable", not a 500
            logger.warning("[transformation_preview] connector unreachable: %s", exc)
            return {"available": False, "reason": f"Could not read sample data: {exc}", "rows": []}
        finally:
            executor.shutdown(wait=False)

        preview_rows = []
        for row in rows:
            source_values = {c: row.get(c) for c in columns}
            output = apply_transformation_preview(ast, source_values)
            preview_rows.append({"source_values": source_values, "output": output})

        logger.info("[transformation_preview] stage=previewed mapping_id=%s edge_id=%s rows=%s",
                    mapping_id, edge_id, len(preview_rows))
        return {"available": True, "reason": None, "rows": preview_rows}
