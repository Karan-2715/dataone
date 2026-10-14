"""
Databricks Ingestion router.

POST /api/v1/databricks/ingest/trigger         — trigger ingestion for a source/target
GET  /api/v1/databricks/ingest/runs/{id}        — get run status
GET  /api/v1/databricks/ingest/runs             — list recent runs
GET  /api/v1/databricks/ingest/pipelines        — list pipeline catalog
POST /api/v1/databricks/ingest/genie            — Genie AI query
POST /api/v1/databricks/ingest/upload-file      — upload CSV/TSV/JSON/JSONL/Parquet/Excel to a UC table
"""
import logging
import re
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.core.config import settings
from app.models.user import User
from app.models.ingestion_run import IngestionRun
from app.services.databricks_ingestion_service import DatabricksIngestionService
from app.services.audit_helper import record_audit
from app.services import file_quality_service
from app.services.upload_quality import profile_rows, to_dq_run
from app.services.data_quality_ingest_service import DataQualityIngestService
from app.services.upload_snapshot_refresh import refresh_snapshots_for_uploaded_table
from app.services.file_ingest_parser import (
    SUPPORTED_EXTENSIONS,
    UnsupportedFileError,
    parse_tabular_file,
    table_name_from_filename,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class TriggerIngestionRequest(BaseModel):
    source_connection_id: int
    target_connection_id: Optional[int] = None
    target_catalog: Optional[str] = None
    target_schema: Optional[str] = None


class GenieRequest(BaseModel):
    question: str
    space_id: Optional[str] = None


def _get_user_token(request: Request) -> Optional[str]:
    """Return the Databricks Apps on-behalf-of token for this user."""
    return request.headers.get("x-forwarded-access-token")


@router.post("/trigger", status_code=202)
def trigger_ingestion(
    req: TriggerIngestionRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Trigger a Databricks ingestion pipeline for the given source/target connection."""
    logger.info(
        "[databricks_ingest] stage=trigger source=%s target=%s actor=%s",
        req.source_connection_id, req.target_connection_id, user.email,
    )
    try:
        result = DatabricksIngestionService.trigger_ingestion(
            source_connection_id=req.source_connection_id,
            target_connection_id=req.target_connection_id,
            target_catalog=req.target_catalog or "workspace",
            target_schema=req.target_schema or "default",
            db=db,
            actor=user.email,
            user_token=None # Use M2M to avoid scope issues,
        )
        record_audit(db, "ingestion_triggered", actor=user.email,
                     payload={"run_id": result.get("id"), "source_type": result.get("source_type")})
        db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("[databricks_ingest] stage=trigger_failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to trigger ingestion: {str(e)}")


@router.get("/runs/{ingestion_run_id}")
def get_run_status(
    ingestion_run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the status of an ingestion run (polls Databricks for live status)."""
    try:
        return DatabricksIngestionService.get_run_status(
            ingestion_run_id=ingestion_run_id,
            db=db,
            user_token=None # Use M2M to avoid scope issues,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("[databricks_ingest] stage=status_failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs")
def list_runs(
    limit: int = 20,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List recent ingestion runs."""
    from app.services.databricks_ingestion_service import _run_to_dict
    runs = db.query(IngestionRun).order_by(IngestionRun.created_at.desc()).limit(limit).all()
    return {"items": [_run_to_dict(r) for r in runs], "total": len(runs)}


@router.get("/pipelines")
def list_pipelines(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all registered ingestion pipeline catalog entries."""
    return {"pipelines": DatabricksIngestionService.list_pipelines(db)}


@router.post("/genie")
def trigger_genie(
    req: GenieRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Trigger a Databricks Genie AI natural language query."""
    return DatabricksIngestionService.trigger_genie(
        question=req.question,
        db=db,
        actor=user.email,
        space_id=req.space_id,
        user_token=None # Use M2M to avoid scope issues,
    )
@router.get("/catalogs")
def get_databricks_catalogs(
    request: Request,
    user: User = Depends(get_current_user)
):
    """Fetch available Databricks Unity Catalog names."""
    try:
        from app.services.databricks_ingestion_service import _get_workspace_client
        wc = _get_workspace_client(_get_user_token(request))
        catalogs = []
        for cat in wc.catalogs.list():
            catalogs.append({"name": cat.name})
        
        logger.info(f"[get_catalogs] Successfully fetched {len(catalogs)} catalogs for {user.email}")
        return {"catalogs": catalogs}
    except Exception as e:
        logger.error(f"[get_catalogs] Failed for user {user.email}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/catalogs/{catalog_name}/schemas")
def get_databricks_schemas(
    catalog_name: str,
    request: Request,
    user: User = Depends(get_current_user)
):
    """Fetch schemas within a Databricks catalog."""
    try:
        from app.services.databricks_ingestion_service import _get_workspace_client
        wc = _get_workspace_client(_get_user_token(request))
        schemas = []
        for schema in wc.schemas.list(catalog_name=catalog_name):
            schemas.append({"name": schema.name})
        
        logger.info(f"[get_schemas] Found {len(schemas)} schemas in {catalog_name}")
        return {"schemas": schemas}
    except Exception as e:
        logger.error(f"[get_schemas] Failed for catalog {catalog_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{ingestion_run_id}/tables")
def get_ingestion_tables(
    ingestion_run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get the list of tables ingested by a specific ingestion run.
    Returns metadata from the schema catalog that was populated after ingestion.
    """
    try:
        from app.services.schema_catalog_service import SchemaCatalogService
        from app.models.connection import DBConnection
        
        # Get the ingestion run
        run = db.query(IngestionRun).filter(IngestionRun.id == ingestion_run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail=f"Ingestion run {ingestion_run_id} not found")
        
        # Get target catalog/schema from the run params
        params = run.trigger_params or {}
        target_catalog = params.get("dataone.target.catalog", "main")
        target_schema = params.get("dataone.target.schema", "dataone_ingested")
        source_db = params.get("dataone.source.database", "")
        
        # Find the Databricks connection for this catalog
        databricks_conn = (
            db.query(DBConnection)
            .filter(
                DBConnection.type == "databricks",
                DBConnection.is_deleted == False
            )
            .first()
        )
        
        if not databricks_conn:
            return {
                "run_id": ingestion_run_id,
                "status": run.status,
                "target_catalog": target_catalog,
                "target_schema": target_schema,
                "tables": [],
                "message": "No Databricks connection found. Tables may not have been cataloged yet."
            }
        
        # Get all tables for this connection
        tables = SchemaCatalogService.get_catalog(db, databricks_conn.id)
        
        # Filter tables that match the target catalog.schema and source prefix
        prefix = f"{target_catalog}.{target_schema}."
        source_prefix = f"{source_db}_" if source_db else ""
        
        ingested_tables = [
            {
                "id": t.id,
                "table_name": t.table_name,
                "short_name": t.table_name.replace(prefix, ""),
                "column_count": len(t.columns),
                "last_scanned_at": t.last_scanned_at.isoformat() if t.last_scanned_at else None,
                "columns": [
                    {
                        "id": col.id,
                        "column_name": col.column_name,
                        "data_type": col.data_type,
                        "nullable": col.nullable,
                        "is_primary_key": col.is_primary_key,
                    }
                    for col in sorted(t.columns, key=lambda c: c.ordinal_position)
                ]
            }
            for t in tables
            if t.table_name.startswith(prefix) and (not source_prefix or source_prefix in t.table_name)
        ]
        
        return {
            "run_id": ingestion_run_id,
            "status": run.status,
            "target_catalog": target_catalog,
            "target_schema": target_schema,
            "source_type": run.source_type,
            "tables": ingested_tables,
            "total": len(ingested_tables),
        }
        
    except Exception as e:
        logger.error("[databricks_ingest] get_ingestion_tables failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_\-]{1,255}$")


def _quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _sql_literal(value: Optional[str]) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


@router.post("/upload-file")
@router.post("/upload-csv")  # legacy alias kept for older clients
async def upload_file(
    request: Request,
    catalog: str = Form(...),
    schema_name: str = Form(default="default", alias="schema"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload a CSV, TSV, JSON, JSONL, Parquet or Excel file to a Databricks Unity Catalog table."""
    from app.services.databricks_ingestion_service import _get_workspace_client

    catalog = catalog.strip()
    schema_name = schema_name.strip()
    for label, ident in (("catalog", catalog), ("schema", schema_name)):
        if not _IDENTIFIER_RE.match(ident):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid {label} name. Use letters, numbers, underscores or hyphens.",
            )
    filename = file.filename or ""
    if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Supported: {', '.join(SUPPORTED_EXTENSIONS)}",
        )

    try:
        content = await file.read()
        try:
            columns, rows = parse_tabular_file(filename, content)
        except UnsupportedFileError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not rows:
            raise HTTPException(status_code=400, detail="File is empty")

        quality = profile_rows(columns, rows)
        table_name = table_name_from_filename(filename)
        full_table_name = f"{_quote_ident(catalog)}.{_quote_ident(schema_name)}.{_quote_ident(table_name)}"
        logger.info("[databricks_ingest] stage=upload_file table=%s rows=%d actor=%s", full_table_name, len(rows), user.email)

        # Interactive uploads run with the signed-in user's Unity Catalog privileges.
        # The app service principal remains reserved for shared/background resources.
        wc = _get_workspace_client(_get_user_token(request))
        
        # We need the warehouse_id
        # We can get it from settings or just find a running warehouse
        from app.core.config import settings
        warehouse_id = getattr(settings, "DATABRICKS_SQL_WAREHOUSE_ID", None)
        if not warehouse_id:
            # Try to auto-detect a warehouse if not configured
            warehouses = list(wc.warehouses.list())
            if not warehouses:
                raise Exception("No Databricks SQL Warehouses available to execute the query.")
            warehouse_id = warehouses[0].id
            
        col_defs = ", ".join([f"{_quote_ident(c)} STRING" for c in columns])
        
        def exec_sql(stmt: str):
            res = wc.statement_execution.execute_statement(
                warehouse_id=warehouse_id,
                statement=stmt,
                wait_timeout="50s"
            )
            
            # If still pending/running, wait for it
            import time
            while res.status and res.status.state and res.status.state.value in ('PENDING', 'RUNNING'):
                time.sleep(2)
                res = wc.statement_execution.get_statement(res.statement_id)
                
            if res.status and res.status.state and res.status.state.value == 'FAILED':
                err = res.status.error.message if res.status.error else "Unknown error"
                raise Exception(f"SQL execution failed: {err} \nStatement: {stmt[:100]}...")
            return res

        # Use an existing catalog/schema as-is; only attempt to create when missing, because the
        # app's service principal usually lacks CREATE CATALOG on the metastore.
        existing_catalogs = [c.name for c in wc.catalogs.list() if c.name]
        if catalog not in existing_catalogs:
            try:
                exec_sql(f"CREATE CATALOG IF NOT EXISTS {_quote_ident(catalog)}")
            except Exception as e:
                if "PERMISSION_DENIED" not in str(e):
                    raise
                available = ", ".join(sorted(existing_catalogs)) or "none"
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Catalog '{catalog}' doesn't exist (or the app has no access to it), and the app "
                        f"isn't allowed to create catalogs. Use an existing catalog. Available: {available}."
                    ),
                )
        existing_schemas = [sc.name for sc in wc.schemas.list(catalog_name=catalog) if sc.name]
        if schema_name not in existing_schemas:
            try:
                exec_sql(f"CREATE SCHEMA IF NOT EXISTS {_quote_ident(catalog)}.{_quote_ident(schema_name)}")
            except Exception as e:
                if "PERMISSION_DENIED" not in str(e):
                    raise
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Schema '{catalog}.{schema_name}' doesn't exist and the app isn't allowed to create it. "
                        f"Grant the app CREATE SCHEMA on catalog '{catalog}', or use an existing schema."
                    ),
                )
        
        # Create Table
        create_sql = f"CREATE TABLE IF NOT EXISTS {full_table_name} ({col_defs})"
        exec_sql(create_sql)
        
        # Insert rows in batches
        batch_size = 5000
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i+batch_size]
            values = []
            for row in batch:
                row_vals = []
                for col in columns:
                    row_vals.append(_sql_literal(row.get(col)))
                values.append("(" + ", ".join(row_vals) + ")")
                
            insert_sql = f"INSERT INTO {full_table_name} ({', '.join(_quote_ident(c) for c in columns)}) VALUES {', '.join(values)}"
            exec_sql(insert_sql)
            
        # Transfer permissions to user so they can see and manage it
        # We use GRANT instead of ALTER OWNER so the App Service Principal 
        # retains permissions to upload additional files to this catalog in the future.
        try:
            exec_sql(f"GRANT ALL PRIVILEGES ON CATALOG {_quote_ident(catalog)} TO `{user.email}`")
        except Exception as e:
            logger.warning(f"[databricks_ingest] Could not grant catalog privileges: {e}")
        try:
            exec_sql(f"GRANT ALL PRIVILEGES ON SCHEMA {_quote_ident(catalog)}.{_quote_ident(schema_name)} TO `{user.email}`")
        except Exception as e:
            logger.warning(f"[databricks_ingest] Could not grant schema privileges: {e}")
        try:
            exec_sql(f"GRANT ALL PRIVILEGES ON TABLE {full_table_name} TO `{user.email}`")
        except Exception as e:
            logger.warning(f"[databricks_ingest] Could not grant table privileges: {e}")
            
        # Record audit
        # Make the new table visible to the mapper/connectors pages, which read cached schema snapshots
        try:
            refresh_snapshots_for_uploaded_table(db, catalog, schema_name, table_name, columns)
        except Exception as e:
            logger.warning("[databricks_ingest] snapshot refresh failed for %s.%s.%s: %s", catalog, schema_name, table_name, e)
            db.rollback()

        # Report the profile to the Data Quality dashboard (one idempotent run per table)
        try:
            DataQualityIngestService(db).ingest(to_dq_run(catalog, schema_name, table_name, quality))
        except Exception as e:
            logger.warning("[databricks_ingest] data-quality report failed for %s.%s.%s: %s", catalog, schema_name, table_name, e)
            db.rollback()

        # Keep the original file in the uploads volume: the Databricks file-quality pipeline profiles it
        # (it adds validity from the raw file and replaces the approximate in-app scores above)
        file_path = None
        try:
            file_path = file_quality_service.save_original_file(schema_name, table_name, filename, content)
        except Exception as e:
            logger.warning("[databricks_ingest] could not save %s to the uploads volume: %s", filename, e)

        record_audit(db, "file_uploaded", actor=user.email, payload={"table": f"{catalog}.{schema_name}.{table_name}", "rows": len(rows), "file": filename,
                                                 "completeness": quality["completeness"], "uniqueness": quality["uniqueness"]})
        db.commit()
            
        return {"message": "Success", "table": f"{catalog}.{schema_name}.{table_name}", "rows": len(rows), "quality": quality,
                "file_quality_path": file_path}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[databricks_ingest] file upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
