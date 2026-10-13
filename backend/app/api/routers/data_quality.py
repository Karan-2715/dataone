"""Databricks data-quality results.

POST /api/v1/data-quality/ingest   — called by the Databricks PySpark job
GET  /api/v1/data-quality/summary  — dashboard read (authenticated user)

The ingest caller is a Databricks job, not a DataOne user, so it
authenticates with a shared key in ``X-DataOne-Ingest-Key`` (the standard
``Authorization`` header is consumed by the Databricks Apps proxy for the
job's own workspace token). The key comes from ``DQ_INGEST_KEY``; when it is
unset the endpoint is disabled rather than open.
"""
import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.api.routers.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.connection import DBConnection
from app.models.user import User
from app.schemas.data_quality import DQCatalogRunIn, DQIngestResult, DQRunIn, DQSummary
from app.services.audit_helper import emit_audit_event
from app.services.catalog_quality_service import CatalogQualityError, profile_catalog_table
from app.services.data_quality_ingest_service import DataQualityIngestService
from app.services import file_quality_service
from app.services.file_quality_service import FileQualityError

logger = logging.getLogger(__name__)
router = APIRouter()

file_check_roles = require_role("admin", "analyst", "editor")


def require_ingest_key(x_dataone_ingest_key: str | None = Header(default=None)) -> None:
    expected = settings.DQ_INGEST_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="Data quality ingest is not configured (DQ_INGEST_KEY unset)")
    if not x_dataone_ingest_key or not hmac.compare_digest(x_dataone_ingest_key, expected):
        raise HTTPException(status_code=401, detail="Invalid ingest key")


@router.post("/ingest", response_model=DQIngestResult, status_code=201, dependencies=[Depends(require_ingest_key)])
def ingest_data_quality(payload: DQRunIn, db: Session = Depends(get_db)):
    result = DataQualityIngestService(db).ingest(payload)
    emit_audit_event(
        db, "data_quality_reported", actor="databricks-dq-job", module="data_quality",
        target_type="data_quality_run", target_id=result.run_id,
        summary=f"{result.table_count} tables, overall score {result.overall_score}",
    )
    db.commit()
    return result


@router.get("/summary", response_model=DQSummary)
def get_data_quality_summary(
    table_limit: int = Query(8, ge=1, le=100),
    scope: str = Query("all", pattern="^(all|files|tables)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return DataQualityIngestService(db).get_summary(
        table_limit=table_limit, ingest_configured=bool(settings.DQ_INGEST_KEY), scope=scope,
    )


@router.post("/catalog/run", response_model=DQIngestResult)
def run_catalog_quality_check(
    payload: DQCatalogRunIn,
    db: Session = Depends(get_db),
    user: User = Depends(file_check_roles),
):
    """Profile one selected Unity Catalog table and persist its result immediately."""
    query = db.query(DBConnection).filter(
        DBConnection.type == "databricks",
        DBConnection.is_deleted == False,  # noqa: E712
    )
    if user.role != "admin":
        query = query.filter(DBConnection.owner_email == user.email)
    connection = query.order_by(DBConnection.id).first()
    if connection is None:
        raise HTTPException(status_code=409, detail="Add a Databricks connection before running a quality check")
    try:
        run = profile_catalog_table(connection, payload.catalog, payload.schema_name, payload.table)
    except CatalogQualityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("[data_quality] stage=catalog_run table=%s.%s.%s failed: %s",
                     payload.catalog, payload.schema_name, payload.table, exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Could not profile the selected table: {exc}") from exc

    result = DataQualityIngestService(db).ingest(run)
    emit_audit_event(
        db, "data_quality_catalog_run", actor=user.email, module="data_quality",
        target_type="table", target_name=f"{payload.catalog}.{payload.schema_name}.{payload.table}",
        target_id=result.run_id, summary=f"overall score {result.overall_score}",
    )
    db.commit()
    return result


# ── Databricks file data-quality pipeline (databricks/file_quality bundle) ──────────────────────────────

@router.post("/files/run", status_code=202)
def run_file_quality_check(user: User = Depends(file_check_roles)):
    """Start the Databricks job that profiles every file in the uploads volume."""
    try:
        run_id = file_quality_service.run_job()
    except FileQualityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("[data_quality] stage=files_run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Could not start the file quality job: {exc}") from exc
    return {"run_id": run_id}


@router.get("/files/runs/{run_id}")
def get_file_quality_run(run_id: int, user: User = Depends(get_current_user)):
    try:
        return file_quality_service.get_run(run_id)
    except Exception as exc:
        logger.error("[data_quality] stage=files_run_status failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Could not read the job run: {exc}") from exc


@router.post("/files/sync")
def sync_file_quality_results(db: Session = Depends(get_db), user: User = Depends(file_check_roles)):
    """Pull the pipeline's per-file results into the Data Quality dashboard (idempotent per pipeline refresh)."""
    try:
        run = file_quality_service.fetch_results()
    except FileQualityError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("[data_quality] stage=files_sync failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Could not read the file quality results: {exc}") from exc
    if run is None:
        return {"synced": False, "tables": 0, "detail": "The pipeline has not profiled any file yet."}
    result = DataQualityIngestService(db).ingest(run)
    emit_audit_event(
        db, "data_quality_files_synced", actor=user.email, module="data_quality",
        target_type="data_quality_run", target_id=result.run_id,
        summary=f"{result.table_count} files, overall score {result.overall_score}",
    )
    db.commit()
    return {"synced": True, "tables": result.table_count, "overall_score": result.overall_score, "run_id": result.run_id}
