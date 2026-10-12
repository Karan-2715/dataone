"""Install / update DataOne's Databricks components in the signed-in user's workspace.

GET  /api/v1/databricks/install/status    — connection + what is installed (and whether it is outdated)
GET  /api/v1/databricks/install/catalogs  — catalogs the user can install into
POST /api/v1/databricks/install           — create schemas/volume/notebooks/secret/job (idempotent)
POST /api/v1/databricks/install/run       — run the installed data-quality job now

Everything runs with the *caller's* Databricks credentials, so Databricks itself
enforces what each user may create. No service principal is involved.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.models.workspace_install import WorkspaceInstall
from app.services.audit_helper import emit_audit_event
from app.services.databricks_auth_service import databricks_auth_service
from app.services.databricks_installer_service import (
    RELEASE, InstallerError, InstallOptions, WorkspaceInstaller, normalize_host,
)

logger = logging.getLogger(__name__)
router = APIRouter()

installer_roles = require_role("admin", "analyst", "editor")


class InstallRequest(BaseModel):
    catalog: str = Field(min_length=1, max_length=128)
    schema_prefix: str = Field(default="dataone", min_length=1, max_length=128)
    secret_scope: str = Field(default="dataone", min_length=1, max_length=128)
    schedule_cron: Optional[str] = Field(default=None, max_length=120)


def get_workspace_client(
    user: User = Depends(installer_roles),
    db: Session = Depends(get_db),
    x_forwarded_access_token: str | None = Header(default=None),
) -> WorkspaceClient:
    """Databricks client acting as the signed-in user.

    Inside a Databricks App the proxy injects a fresh user token on every
    request; otherwise the token stored at OAuth sign-in is used (refreshed when expired).
    """
    host = normalize_host(settings.DATABRICKS_WORKSPACE_URL or settings.DATABRICKS_HOST)
    if not host:
        raise HTTPException(409, "No Databricks workspace is configured (DATABRICKS_WORKSPACE_URL)")

    token = x_forwarded_access_token
    if not token:
        if not user.databricks_access_token:
            raise HTTPException(401, "Sign in with Databricks to install into your workspace")
        token = user.databricks_access_token
        try:
            token = databricks_auth_service.ensure_token_valid(user, db)
        except Exception as exc:
            logger.warning("[workspace_install] token refresh failed for %s: %s", user.email, exc)
            raise HTTPException(401, "Your Databricks session expired; sign in with Databricks again") from exc
    return WorkspaceClient(host=host, token=token, auth_type="pat")


def _record(db: Session, host: str, req: InstallRequest, user: User, outcome) -> WorkspaceInstall:
    row = db.query(WorkspaceInstall).filter(WorkspaceInstall.workspace_host == host).first()
    if row is None:
        row = WorkspaceInstall(workspace_host=host)
        db.add(row)
    row.catalog, row.schema_prefix, row.secret_scope = req.catalog, req.schema_prefix, req.secret_scope
    row.notebook_folder = outcome.notebook_folder
    row.job_id, row.release, row.installed_by = outcome.job_id, RELEASE, user.email
    row.steps = outcome.as_dict()["steps"]
    row.installed_at = datetime.now(timezone.utc)
    return row


def _install_view(row: WorkspaceInstall | None) -> dict | None:
    if row is None:
        return None
    return {
        "catalog": row.catalog, "schema_prefix": row.schema_prefix, "job_id": row.job_id,
        "release": row.release, "installed_by": row.installed_by, "installed_at": row.installed_at,
        "notebook_folder": row.notebook_folder,
    }


@router.get("/status")
def install_status(
    db: Session = Depends(get_db),
    user: User = Depends(installer_roles),
    x_forwarded_access_token: str | None = Header(default=None),
):
    host = normalize_host(settings.DATABRICKS_WORKSPACE_URL or settings.DATABRICKS_HOST)
    row = db.query(WorkspaceInstall).filter(WorkspaceInstall.workspace_host == host).first() if host else None
    return {
        "workspace_host": host,
        "signed_in_with_databricks": bool(x_forwarded_access_token or user.databricks_access_token),
        "ingest_key_configured": bool(settings.DQ_INGEST_KEY),
        "current_release": RELEASE,
        "installed": _install_view(row),
        "up_to_date": bool(row and row.release == RELEASE),
    }


@router.get("/catalogs")
def list_catalogs(w: WorkspaceClient = Depends(get_workspace_client)):
    try:
        catalogs = list(w.catalogs.list())
    except DatabricksError as exc:
        raise HTTPException(502, f"Could not list catalogs: {exc}") from exc
    # Schemas can only be created in managed catalogs (not system / foreign / shared ones).
    names = sorted(c.name for c in catalogs
                   if c.name and (c.catalog_type is None or c.catalog_type.value == "MANAGED_CATALOG"))
    return {"catalogs": names}


@router.post("")
def install(
    req: InstallRequest,
    db: Session = Depends(get_db),
    user: User = Depends(installer_roles),
    w: WorkspaceClient = Depends(get_workspace_client),
):
    opts = InstallOptions(catalog=req.catalog, schema_prefix=req.schema_prefix,
                          secret_scope=req.secret_scope, schedule_cron=req.schedule_cron)
    try:
        outcome = WorkspaceInstaller(w, user.email).install(opts)
    except InstallerError as exc:
        raise HTTPException(400, str(exc)) from exc

    host = normalize_host(w.config.host)
    if outcome.ok:
        _record(db, host, req, user, outcome)
    emit_audit_event(
        db, "workspace_install", actor=user.email, module="databricks",
        target_type="workspace", target_name=host, outcome="success" if outcome.ok else "failure",
        summary=f"release {RELEASE}: " + ", ".join(f"{s.step}={s.status}" for s in outcome.steps),
    )
    db.commit()
    return {**outcome.as_dict(), "release": RELEASE}


@router.post("/run")
def run_quality_job(
    db: Session = Depends(get_db),
    user: User = Depends(installer_roles),
    w: WorkspaceClient = Depends(get_workspace_client),
):
    host = normalize_host(w.config.host)
    row = db.query(WorkspaceInstall).filter(WorkspaceInstall.workspace_host == host).first()
    if row is None or row.job_id is None:
        raise HTTPException(409, "DataOne is not installed in this workspace yet")
    installer = WorkspaceInstaller(w, user.email)
    try:
        run_id = installer.run_job_now(row.job_id)
    except DatabricksError as exc:
        raise HTTPException(502, f"Could not start the job: {exc}") from exc
    emit_audit_event(db, "workspace_job_run", actor=user.email, module="databricks",
                     target_type="job", target_id=row.job_id, summary=f"run {run_id}")
    db.commit()
    return {"run_id": run_id, "run_url": installer.job_run_url(row.job_id, run_id)}
