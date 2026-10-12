"""Bridge to the Databricks file data-quality pipeline (``databricks/file_quality`` bundle).

  * ``save_original_file``  — Quick Upload keeps the original file in the uploads volume so the pipeline can profile it
  * ``run_job`` / ``get_run`` — start and poll ``dataone-file-quality-job``
  * ``fetch_results``       — read ``file_quality_results`` (completeness / uniqueness / validity / freshness per file)
                              and shape it as a data-quality dashboard run

The app's service principal needs: WRITE VOLUME on the uploads volume, SELECT on the results schema, CAN_MANAGE_RUN
on the job (all granted by the bundle) and CAN USE on a SQL warehouse.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.schemas.data_quality import DQIssue, DQRunIn, DQTableIn

logger = logging.getLogger(__name__)

# Keep in sync with FORMATS in databricks/file_quality/src/file_quality_pipeline.py
PIPELINE_EXTENSIONS = (".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".parquet", ".avro", ".orc")
FILES_SOURCE = "databricks-files"

_TABLE_RE = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+){2}$")
_RESULT_COLUMNS = (
    "table_name", "row_count", "column_count", "completeness", "uniqueness", "validity", "freshness",
    "last_modified", "issues", "profiled_at",
)


class FileQualityError(RuntimeError):
    """The Databricks side of the file quality check is missing, misconfigured or failed."""


def _client():
    from app.services.databricks_ingestion_service import _get_workspace_client
    return _get_workspace_client(None)


def supports_pipeline(filename: str) -> bool:
    return (filename or "").lower().endswith(PIPELINE_EXTENSIONS)


def save_original_file(schema_name: str, table_name: str, filename: str, content: bytes) -> Optional[str]:
    """Write the uploaded file to ``<volume>/<schema>/<table><ext>``; returns the path, or None if the format
    is not profiled by the pipeline. Re-uploading the same table replaces the file."""
    if not supports_pipeline(filename):
        return None
    ext = os.path.splitext(filename)[1].lower()
    base = settings.DQ_FILES_VOLUME_PATH.rstrip("/")
    path = f"{base}/{schema_name}/{table_name}{ext}"
    logger.info("[file_quality] stage=save_original path=%s bytes=%d", path, len(content))
    _client().files.upload(path, io.BytesIO(content), overwrite=True)
    return path


def run_job() -> int:
    wc = _client()
    jobs = list(wc.jobs.list(name=settings.DQ_FILES_JOB_NAME))
    if not jobs:
        raise FileQualityError(
            f"Databricks job '{settings.DQ_FILES_JOB_NAME}' was not found. Deploy it with "
            "`databricks bundle deploy` from databricks/file_quality."
        )
    logger.info("[file_quality] stage=run_job job_id=%s", jobs[0].job_id)
    return wc.jobs.run_now(job_id=jobs[0].job_id).run_id


def get_run(run_id: int) -> Dict[str, Any]:
    state = _client().jobs.get_run(run_id).state
    life_cycle = state.life_cycle_state.value if state and state.life_cycle_state else None
    result = state.result_state.value if state and state.result_state else None
    return {
        "run_id": run_id,
        "life_cycle_state": life_cycle,
        "result_state": result,
        "finished": life_cycle in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"),
        "success": result == "SUCCESS",
        "message": (state.state_message if state else None) or "",
    }


def _quote_table(full_name: str) -> str:
    if not _TABLE_RE.match(full_name):
        raise FileQualityError(f"DQ_FILES_RESULTS_TABLE must be catalog.schema.table, got {full_name!r}")
    return ".".join("`" + part + "`" for part in full_name.split("."))


def _run_sql(wc, statement: str) -> List[List[Optional[str]]]:
    warehouse_id = getattr(settings, "DATABRICKS_SQL_WAREHOUSE_ID", None)
    if not warehouse_id:
        warehouses = list(wc.warehouses.list())
        if not warehouses:
            raise FileQualityError("No Databricks SQL warehouse is available to read the file quality results.")
        warehouse_id = warehouses[0].id
    res = wc.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=statement, wait_timeout="50s")
    while res.status and res.status.state and res.status.state.value in ("PENDING", "RUNNING"):
        time.sleep(2)
        res = wc.statement_execution.get_statement(res.statement_id)
    if not (res.status and res.status.state and res.status.state.value == "SUCCEEDED"):
        err = res.status.error.message if res.status and res.status.error else "unknown error"
        raise FileQualityError(f"Reading file quality results failed: {err}")
    return (res.result.data_array if res.result and res.result.data_array else [])


def _num(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    return max(0.0, min(100.0, float(value)))


def _int(value: Optional[str]) -> Optional[int]:
    return None if value in (None, "") else int(float(value))


def _dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _issues(raw: Optional[str]) -> List[DQIssue]:
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[file_quality] could not parse issues JSON: %.80s", raw)
        return []
    return [
        DQIssue(column=(i.get("column") or None) and str(i["column"])[:255],
                check=str(i.get("check") or "check")[:64], detail=str(i.get("detail") or "")[:500])
        for i in items[:20] if isinstance(i, dict)
    ]


def rows_to_run(rows: List[List[Optional[str]]], results_table: str) -> Optional[DQRunIn]:
    """Shape ``file_quality_results`` rows (column order = ``_RESULT_COLUMNS``) as a dashboard run."""
    if not rows:
        return None
    tables, profiled = [], []
    for r in rows:
        rec = dict(zip(_RESULT_COLUMNS, r))
        profiled.append(_dt(rec["profiled_at"]) or datetime.now(timezone.utc))
        tables.append(DQTableIn(
            table_name=str(rec["table_name"])[:512],
            row_count=_int(rec["row_count"]), column_count=_int(rec["column_count"]),
            completeness=_num(rec["completeness"]), uniqueness=_num(rec["uniqueness"]),
            validity=_num(rec["validity"]), freshness=_num(rec["freshness"]),
            last_modified=_dt(rec["last_modified"]), issues=_issues(rec["issues"]),
        ))
    completed_at = max(profiled)
    catalog, schema_name, _ = results_table.split(".")
    return DQRunIn(
        run_id=f"files:{completed_at.isoformat()}",  # one run per pipeline refresh; re-syncing is idempotent
        source=FILES_SOURCE, catalog=catalog, schema_name=schema_name,
        completed_at=completed_at, tables=tables[:500],
    )


def fetch_results() -> Optional[DQRunIn]:
    """Read the pipeline's results table. None when the pipeline has not profiled any file yet."""
    table = settings.DQ_FILES_RESULTS_TABLE
    statement = f"SELECT {', '.join(_RESULT_COLUMNS)} FROM {_quote_table(table)}"
    logger.info("[file_quality] stage=fetch_results table=%s", table)
    return rows_to_run(_run_sql(_client(), statement), table)
