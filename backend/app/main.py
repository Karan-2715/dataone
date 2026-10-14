"""
dataPlane API — main entry point.

Startup lifecycle:
  1. Create DB tables.
  2. Seed the admin user.
  3. Register Celery beat schedules.
  4. Seed the RBAC catalog.

Sample/demo data (CRM, DW, E‑Commerce, Finance, HR, E2E retail) is no longer
seeded automatically here — it's opt-in, loaded on demand via
`app.api.routers.demo_data` (see `app.services.demo_data_service`).
"""
import logging
import logging.config
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from app.api.routers import connectors, schema, askdata, llm
from app.api.routers import tasks as tasks_router
from app.api.routers import audit as audit_router
from app.api.routers import auth as auth_router
from app.api.routes import databricks_auth
from app.api.routers import autopilot as autopilot_router
from app.api.routers import mappings as mappings_router
from app.api.routers import schema_catalog as schema_catalog_router
from app.api.routers import search as search_router
from app.api.routers import dashboard as dashboard_router
from app.api.routers import query_studio as query_studio_router
from app.api.routers import viz as viz_router
from app.api.routers import viz_ai as viz_ai_router
from app.api.routers import roles as roles_router
from app.api.routers import users_admin as users_admin_router
from app.api.routers import policies as policies_router
from app.api.routers import agentic_dba as agentic_dba_router
from app.api.routers import notifications as notifications_router
from app.api.routers import governance as governance_router
from app.api.routers import workspace_layout as workspace_layout_router
from app.api.routers import demo_data as demo_data_router
from app.api.routers import transformation as transformation_router
from app.core.celery_app import celery_app  # noqa: F401  (registers tasks on import)
from app.core.audit_guard import install_audit_append_only_guard
from app.core.config import settings
from app.core.database import Base, engine, SessionLocal, database_runtime
from app.models.connection import DBConnection  # ensure models loaded
from app.models.audit import AuditLog  # noqa: F401
from app.models.query_history import QueryHistory  # noqa: F401
from app.models.chat_session import ChatMessage  # noqa: F401
from app.models.schema_snapshot import SchemaSnapshot  # noqa: F401
from app.models.drift_event import DriftEvent  # noqa: F401
from app.models.schema_catalog import CatalogTable, CatalogColumn, CatalogForeignKey  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.autopilot import AutopilotRun, AutopilotLog  # noqa: F401

from app.models.pipeline import Pipeline, PipelineRun, PipelineRunStep, RetryPolicy, Schedule  # noqa: F401
from app.models.mapping import (  # noqa: F401
    Mapping, MappingVersion, FieldMapping, AISuggestion,
)
from app.models.saved_query import SavedQuery  # noqa: F401
from app.models.security import (  # noqa: F401
    Role, Permission, RolePermission, UserRole, MaskingPolicy, RowAccessPolicy,
)
from app.models.schema_design_plan import SchemaDesignPlan  # noqa: F401
from app.models.notification_setting import NotificationSetting  # noqa: F401
from app.models.in_app_notification import InAppNotification, InAppNotificationRead  # noqa: F401
from app.models.connection_secret import ConnectionSecret  # noqa: F401
from app.models.ingestion_run import IngestionRun, IngestionPipelineCatalog  # noqa: F401
from app.models.data_quality import DataQualityRun, DataQualityTableResult  # noqa: F401
from app.models.workspace_install import WorkspaceInstall  # noqa: F401
from app.models.oauth_login_state import OAuthLoginState  # noqa: F401
from app.api.routers import databricks_ingest as databricks_ingest_router
from app.api.routers import data_quality as data_quality_router
from app.api.routers import genie_chat as genie_chat_router
from app.api.routers import workspace_install as workspace_install_router

# ── Structured logging setup ──────────────────────────────────────────────────
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        }
    },
    "root": {
        "level": settings.LOG_LEVEL,
        "handlers": ["console"],
    },
})
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    from sqlalchemy import text

    # Gunicorn starts more than one application worker. Lifespan runs in
    # every worker, so schema creation must be serialized across processes
    # (and across API replicas). PostgreSQL advisory locks are
    # session-scoped and automatically released if a failed worker exits
    # before reaching the explicit unlock below.
    startup_lock_connection = engine.connect()
    startup_lock_key = 0x444154414F4E45  # ASCII-ish stable key for "DATAONE"
    if engine.dialect.name == "postgresql":
        startup_lock_connection.execute(
            text("SELECT pg_advisory_lock(:key)"), {"key": startup_lock_key},
        )
        logger.info("[startup] acquired initialization lock")

    # 1. Create tables
    Base.metadata.create_all(bind=engine)
    # Additive compatibility upgrade: create_all does not add columns to an
    # existing deployment. `environment` is a label, never an isolation key.
    from sqlalchemy import inspect
    
    # Add Databricks OAuth columns to users table
    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    databricks_oauth_columns = {
        "databricks_user_id": "VARCHAR",
        "databricks_access_token": "TEXT",
        "databricks_refresh_token": "TEXT",
        "databricks_token_expires_at": "TIMESTAMP WITH TIME ZONE",
        "llm_model": "VARCHAR",
    }
    for column_name, column_type in databricks_oauth_columns.items():
        if column_name not in user_columns:
            startup_lock_connection.execute(text(
                f"ALTER TABLE users ADD COLUMN {column_name} {column_type}"
            ))
            startup_lock_connection.commit()
            logger.info(f"[startup] added users.{column_name}")
    
    # Make hashed_password nullable for OAuth users
    user_columns_info = inspect(engine).get_columns("users")
    password_column = next((col for col in user_columns_info if col["name"] == "hashed_password"), None)
    if password_column and not password_column.get("nullable", False):
        startup_lock_connection.execute(text(
            "ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL"
        ))
        startup_lock_connection.commit()
        logger.info("[startup] made users.hashed_password nullable for OAuth users")
    
    # Add full_name column if missing
    if "full_name" not in user_columns:
        startup_lock_connection.execute(text(
            "ALTER TABLE users ADD COLUMN full_name VARCHAR"
        ))
        startup_lock_connection.commit()
        logger.info("[startup] added users.full_name")
    
    connection_columns = {column["name"] for column in inspect(engine).get_columns("connections")}
    if "environment" not in connection_columns:
        startup_lock_connection.execute(text(
            "ALTER TABLE connections ADD COLUMN environment VARCHAR NOT NULL DEFAULT 'dev'"
        ))
        startup_lock_connection.commit()
        logger.info("[startup] added connections.environment label")
    if "owner_email" not in connection_columns:
        startup_lock_connection.execute(text(
            "ALTER TABLE connections ADD COLUMN owner_email VARCHAR"
        ))
        # Backfill: existing rows' creator becomes their owner; rows with no
        # recorded creator (e.g. old demo-seeded rows) fall back to the
        # bootstrap admin (tenant_isolation_tasks slice #1 — connections only).
        startup_lock_connection.execute(text(
            "UPDATE connections SET owner_email = created_by "
            "WHERE owner_email IS NULL AND created_by IS NOT NULL AND created_by != ''"
        ))
        startup_lock_connection.execute(text(
            "UPDATE connections SET owner_email = 'admin@dataplane.ai' "
            "WHERE owner_email IS NULL"
        ))
        startup_lock_connection.commit()
        logger.info("[startup] added connections.owner_email and backfilled from created_by")
    connection_indexes = inspect(engine).get_indexes("connections")
    name_index = next(
        (idx for idx in connection_indexes if idx["name"] == "uq_connection_name_active"),
        None,
    )
    if name_index and "owner_email" not in name_index["column_names"]:
        startup_lock_connection.execute(text("DROP INDEX uq_connection_name_active"))
        startup_lock_connection.execute(text(
            "CREATE UNIQUE INDEX uq_connection_name_active ON connections (owner_email, name) "
            "WHERE NOT is_deleted"
        ))
        startup_lock_connection.commit()
        logger.info("[startup] widened uq_connection_name_active to (owner_email, name)")
    suggestion_columns = {
        column["name"] for column in inspect(engine).get_columns("ai_suggestions")
    }
    json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
    suggestion_upgrades = {
        "components": json_type,
        "suggested_transformation": json_type,
        "transformation_note": "TEXT",
    }
    for column_name, column_type in suggestion_upgrades.items():
        if column_name not in suggestion_columns:
            startup_lock_connection.execute(text(
                f"ALTER TABLE ai_suggestions ADD COLUMN {column_name} {column_type}"
            ))
            startup_lock_connection.commit()
            logger.info("[startup] added ai_suggestions.%s", column_name)
    pipeline_columns = {
        column["name"] for column in inspect(engine).get_columns("pipelines")
    }
    if "execution_mode" not in pipeline_columns:
        startup_lock_connection.execute(text(
            "ALTER TABLE pipelines ADD COLUMN execution_mode VARCHAR NOT NULL DEFAULT 'manual'"
        ))
        startup_lock_connection.commit()
        logger.info("[startup] added pipelines.execution_mode")
    if "load_strategy" not in pipeline_columns:
        startup_lock_connection.execute(text(
            "ALTER TABLE pipelines ADD COLUMN load_strategy VARCHAR"
        ))
        startup_lock_connection.commit()
        logger.info("[startup] added pipelines.load_strategy")
        
    try:
        snapshot_columns = {
            column["name"] for column in inspect(engine).get_columns("schema_snapshots")
        }
        if snapshot_columns:
            snapshot_upgrades = {
                "connection_name": "VARCHAR",
                "schema_hash": "VARCHAR",
                "captured_at": "TIMESTAMP"
            }
            for col_name, col_type in snapshot_upgrades.items():
                if col_name not in snapshot_columns:
                    startup_lock_connection.execute(text(
                        f"ALTER TABLE schema_snapshots ADD COLUMN {col_name} {col_type}"
                    ))
                    startup_lock_connection.commit()
                    logger.info(f"[startup] added schema_snapshots.{col_name}")
    except Exception as exc:
        logger.warning(f"[startup] error upgrading schema_snapshots: {exc}")
        
    install_audit_append_only_guard(engine)

    # 2. Seed an optional password-based administrator. Marketplace installs
    # use Databricks SSO, so no shared default password is created.
    from app.services.auth_service import AuthService
    db = SessionLocal()
    try:
        if not db.query(User).first() and settings.ADMIN_DEFAULT_PASSWORD:
            db.add(User(
                email="admin@dataplane.ai",
                hashed_password=AuthService.hash_password(settings.ADMIN_DEFAULT_PASSWORD),
                role="admin", is_active=True,
            ))
            db.commit()
        elif not db.query(User).first():
            logger.info("[startup] password admin bootstrap skipped; Databricks SSO is enabled")
    finally:
        db.close()

    # 3. Register Celery beat schedules
    from app.core.scheduler import setup_schedule_tasks
    setup_schedule_tasks()

    # 4. Seed RBAC
    from app.services.rbac_service import (
        seed_permission_catalog, seed_default_roles, backfill_user_roles,
    )
    db = SessionLocal()
    try:
        seed_permission_catalog(db)
        seed_default_roles(db)
        backfill_user_roles(db)
    finally:
        db.close()

    # 5. Auto-discover Databricks workspace (if running as Databricks App)
    from app.services.databricks_autodiscovery import DatabricksAutoDiscoveryService
    db = SessionLocal()
    try:
        DatabricksAutoDiscoveryService.setup_auto_discovery(db)
    finally:
        db.close()

    # 6. Pre-warm SchemaSnapshots for all existing Databricks connections in the background.
    #    This ensures that the Schema Mapper is always instant after startup — no matter
    #    whether DATABRICKS_AUTO_DISCOVER is set or not.
    import threading

    def _prewarm_snapshots():
        import hashlib
        import json as _json
        from app.core.database import SessionLocal as _SL
        from app.models.connection import DBConnection as _DBC
        from app.models.schema_snapshot import SchemaSnapshot as _SS
        from app.services.schema_service import SchemaService as _SchemaService

        bg_db = _SL()
        try:
            databricks_conns = (
                bg_db.query(_DBC)
                .filter(_DBC.type == "databricks", _DBC.is_deleted == False)  # noqa: E712
                .all()
            )
            for conn in databricks_conns:
                # Skip if a snapshot already exists for this connection
                existing = (
                    bg_db.query(_SS)
                    .filter(_SS.connection_id == conn.id)
                    .first()
                )
                if existing:
                    logger.info("[prewarm] Snapshot already exists for connector %d (%s) — skipping", conn.id, conn.name)
                    continue
                try:
                    logger.info("[prewarm] Building SchemaSnapshot for connector %d (%s)…", conn.id, conn.name)
                    schema_data = _SchemaService.get_full_schema(conn)
                    normalized = _json.dumps(schema_data, sort_keys=True, default=str)
                    snap = _SS(
                        connection_id=conn.id,
                        connection_name=conn.name,
                        schema_hash=hashlib.sha256(normalized.encode()).hexdigest(),
                        schema_json=schema_data,
                    )
                    bg_db.add(snap)
                    bg_db.commit()
                    logger.info("[prewarm] ✅ SchemaSnapshot saved for connector %d (%s)", conn.id, conn.name)
                except Exception as exc:
                    logger.warning("[prewarm] Could not pre-warm connector %d (%s): %s", conn.id, conn.name, exc)
                    bg_db.rollback()
        finally:
            bg_db.close()

    threading.Thread(target=_prewarm_snapshots, daemon=True, name="schema-prewarm").start()
    logger.info("[startup] Schema pre-warm thread started in background")

    if engine.dialect.name == "postgresql":
        startup_lock_connection.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": startup_lock_key},
        )
        logger.info("[startup] released initialization lock")
    startup_lock_connection.close()

    yield


app = FastAPI(
    title="DataOne API",
    description="Agentic DBA & Data Transformation Platform",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# When running as Databricks Apps, CORS_ALLOWED_ORIGINS is set by start.py to
# the explicit frontend + backend URLs so browsers accept cross-origin requests
# from the frontend app. Falls back to wildcard for local/Docker deployments.
_cors_origins_env = os.environ.get("CORS_ALLOWED_ORIGINS", "")
if _cors_origins_env:
    _allowed_origins: list[str] = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
else:
    _allowed_origins = ["*"]
logger.info("[startup] CORS allowed origins: %s", _allowed_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,  # Bearer-token auth — no cookies, no credentials header needed
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "HEAD"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ── Request logging middleware ────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    start = time.monotonic()
    
    # Never log forwarded headers: Databricks injects user access tokens there.
    origin = request.headers.get("origin")
    logger.info(
        f"[{request_id}] Incoming request: {request.method} {request.url.path}",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "origin": origin,
        }
    )
    
    # Handle preflight OPTIONS requests explicitly
    if request.method == "OPTIONS":
        logger.info(f"[{request_id}] CORS preflight request from origin: {origin}")
    
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000)
    
    logger.info(
        f"[{request_id}] Response: {response.status_code} ({duration_ms}ms)",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "origin": origin,
        },
    )
    
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time"] = f"{duration_ms}ms"
    return response

# Include Routers
app.include_router(connectors.router, prefix="/api/v1/connectors", tags=["Connectors"])
app.include_router(schema.router, prefix="/api/v1/schema", tags=["Schema Intelligence"])
app.include_router(llm.router, prefix="/api/v1/llm", tags=["LLM"])
app.include_router(query_studio_router.router, prefix="/api/v1/query-studio", tags=["Query Studio"])
app.include_router(askdata.router, prefix="/api/v1/askdata", tags=["AskData Bot"])
app.include_router(mappings_router.router, prefix="/api/v1/mappings", tags=["Schema Mapper — Mappings"])
app.include_router(schema_catalog_router.router, prefix="/api/v1/catalog", tags=["Schema Catalog"])
app.include_router(search_router.router, prefix="/api/v1/search", tags=["Global Search"])
app.include_router(tasks_router.router, prefix="/api/v1/tasks", tags=["Tasks"])
app.include_router(audit_router.router, prefix="/api/v1/audit", tags=["Audit Trail"])
app.include_router(auth_router.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(databricks_auth.router, prefix="/api/v1", tags=["Databricks Auth"])
app.include_router(autopilot_router.router, prefix="/api/v1/autopilot", tags=["AI Autopilot"])
app.include_router(dashboard_router.router, prefix="/api/v1/dashboard", tags=["Dashboard"])
app.include_router(viz_router.router, prefix="/api/v1/viz", tags=["Visualize"])
app.include_router(viz_ai_router.router, prefix="/api/v1/viz", tags=["Visualize AI"])
app.include_router(roles_router.router, prefix="/api/v1/roles", tags=["Security — Roles"])
app.include_router(users_admin_router.router, prefix="/api/v1/users", tags=["Security — Users"])
app.include_router(policies_router.router, prefix="/api/v1/policies", tags=["Security — Policies"])
app.include_router(agentic_dba_router.router, prefix="/api/v1/agentic-dba", tags=["Agentic DBA Copilot"])
app.include_router(notifications_router.router, prefix="/api/v1/notifications", tags=["Notifications"])
app.include_router(governance_router.router, prefix="/api/v1/governance", tags=["Governance Intelligence Center"])
app.include_router(workspace_layout_router.router, prefix="/api/v1/workspace-layout", tags=["Dockable Workspace Shell"])
app.include_router(demo_data_router.router, prefix="/api/v1/demo-data", tags=["Demo Data"])
app.include_router(databricks_ingest_router.router, prefix="/api/v1/databricks/ingest", tags=["Databricks Ingestion"])
app.include_router(workspace_install_router.router, prefix="/api/v1/databricks/install", tags=["Databricks Workspace Install"])
app.include_router(data_quality_router.router, prefix="/api/v1/data-quality", tags=["Data Quality (Databricks)"])
app.include_router(genie_chat_router.router, prefix="/api/v1/genie", tags=["Genie Chat (NL to SQL)"])
app.include_router(transformation_router.router, prefix="/api/v1/transformation", tags=["Transformation Copilot"])


@app.get("/health")
def health_check():
    """Deep health check: verifies DB and Redis connectivity."""
    checks: dict = {}
    overall = "healthy"

    try:
        db = SessionLocal()
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db.close()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
        overall = "degraded"

    # Check Redis/Celery only if not disabled for Databricks Apps
    if os.getenv("DISABLE_CELERY") == "true":
        checks["redis"] = "disabled (Databricks Apps mode)"
        checks["celery"] = "disabled (Databricks Apps mode)"
    else:
        try:
            import redis as _redis
            broker_url = settings.CELERY_BROKER_URL
            if broker_url and broker_url != "redis://disabled":
                r = _redis.from_url(broker_url, socket_connect_timeout=2)
                r.ping()
                checks["redis"] = "ok"
            else:
                checks["redis"] = "disabled"
        except Exception as exc:
            checks["redis"] = f"error: {exc}"
            if overall == "healthy":
                overall = "degraded"

    status_code = 200 if overall == "healthy" else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall,
            "service": "DataOne API",
            "version": "1.0.0",
            "checks": checks,
            "mode": "databricks_app" if os.getenv("DATABRICKS_APP_PORT") else "standalone",
            "database_mode": database_runtime.mode,
            "persistent_state": database_runtime.persistent,
        },
    )



# ── Serve Next.js static frontend (unified deployment) ────────────────────────
# When FRONTEND_OUT_DIR is set (by start.py), FastAPI serves the pre-built
# Next.js export at every non-API path. This makes frontend + backend run as
# a single Databricks App at the same origin, eliminating cross-origin issues.
_frontend_out = os.environ.get("FRONTEND_OUT_DIR", "")
if not _frontend_out:
    # Fallback to computing the path relative to main.py
    # __file__ = backend/app/main.py
    _repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _fallback_out = os.path.join(_repo_root, "frontend", "out")
    if os.path.isdir(_fallback_out):
        _frontend_out = _fallback_out

if _frontend_out and os.path.isdir(_frontend_out):
    logger.info("[startup] Unified mode: serving frontend from %s", _frontend_out)

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
    async def _serve_frontend(full_path: str):
        """
        Catch-all: serve Next.js static export for any path that is not an
        API route. Routes are matched in registration order in FastAPI, so all
        /api/v1/* routes registered above take priority over this catch-all.
        """
        # Exact file (JS, CSS, images, etc.)
        fp = os.path.join(_frontend_out, full_path)
        if os.path.isfile(fp):
            return FileResponse(fp)
            
        # Next.js App Router static export produces .html files for routes
        html_path = os.path.join(_frontend_out, f"{full_path.rstrip('/')}.html")
        if os.path.isfile(html_path):
            return FileResponse(html_path)
            
        # Next.js static export fallback (if trailingSlash: true was used)
        ip = os.path.join(_frontend_out, full_path.rstrip("/"), "index.html")
        if os.path.isfile(ip):
            return FileResponse(ip)
            
        # SPA fallback: return root index.html for client-side routing
        root_index = os.path.join(_frontend_out, "index.html")
        if os.path.isfile(root_index):
            return FileResponse(root_index)
        raise HTTPException(status_code=404, detail="Not found")
else:
    logger.info("[startup] API-only mode (FRONTEND_OUT_DIR not set or missing)")
