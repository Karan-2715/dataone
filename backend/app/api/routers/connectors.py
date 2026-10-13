import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.api.routers.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.connection import DBConnection
from app.models.user import User
from app.schemas.connection import (
    ConnectionCreate,
    ConnectionEnvironmentUpdate,
    ConnectionResponse,
    ConnectorTypeMetadata,
    HealthSummary,
    TestConnectionResponse,
    TestDiagnostics,
    TestErrorDetail,
)
from app.services import connector_catalog
from app.services.audit_helper import record_audit
from app.services.connection_service import ConnectionService
from app.services.schema_service import SchemaService

logger = logging.getLogger(__name__)
router = APIRouter()


def _actor(user) -> str:
    return getattr(user, "email", None) or "system"


def _owner_scope(user: User) -> str | None:
    """Per-user connection isolation (tenant_isolation_tasks slice #1):
    `None` means unscoped — admins see/manage every connection; every other
    role is limited to rows it owns."""
    return None if user.role == "admin" else user.email


def _to_response(conn: DBConnection, *, unity_catalog: Optional[dict] = None) -> ConnectionResponse:
    """Single choke point for serializing a connection — always redacts
    secret config values (TRD FR3: secrets are never returned after save)."""
    return ConnectionResponse(
        id=conn.id,
        name=conn.name,
        type=conn.type,
        environment=conn.environment or "dev",
        config=connector_catalog.redact_config(conn.type, conn.config),
        health_status=conn.health_status or "unknown",
        last_tested_at=conn.last_tested_at,
        last_test_error=conn.last_test_error,
        is_deleted=bool(conn.is_deleted),
        created_by=conn.created_by,
        owner_email=conn.owner_email,
        created_at=conn.created_at,
        unity_catalog=unity_catalog,
    )


# ── Static routes (must precede /{id}) ─────────────────────────

@router.get("/workspace", response_model=ConnectionResponse)
def get_workspace_connection(db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    """Auto-detect Databricks workspace connection when running as Databricks App."""
    
    # Check if we're running in Databricks Apps environment
    if not os.getenv("DATABRICKS_APP_PORT"):
        raise HTTPException(status_code=404, detail="Not running in Databricks Apps environment")
    
    # Try to detect Databricks workspace details from environment
    workspace_hostname = os.getenv("DATABRICKS_HOST") or os.getenv("DATABRICKS_SERVER_HOSTNAME")
    if not workspace_hostname:
        # Try to extract from APP_URL
        app_url = os.getenv("APP_URL", "")
        if "databricksapps.com" in app_url:
            # Extract workspace ID from URL pattern
            # https://dataonetest-7474652115156015.aws.databricksapps.com
            # -> adb-7474652115156015.aws.databricks.net
            import re
            match = re.search(r'-(\d+)\.([^.]+)\.databricksapps\.com', app_url)
            if match:
                workspace_id, region = match.groups()
                workspace_hostname = f"adb-{workspace_id}.{region}.databricks.net"
    
    if not workspace_hostname:
        raise HTTPException(status_code=404, detail="Unable to auto-detect workspace details")
    
    # Create a virtual workspace connection response (not stored in DB)
    return ConnectionResponse(
        id=-1,  # Special ID for workspace connection
        name="Current Workspace",
        type="databricks",
        environment="workspace",
        config={
            "server_hostname": workspace_hostname,
            "http_path": "/sql/1.0/warehouses/auto",  # Will be detected at runtime
            "catalog": "main",
            "schema": "default"
        },
        created_by="system",
        last_tested=None,
        is_deleted=False,
        databricks_detected=True
    )


@router.get("/types", response_model=Dict[str, ConnectorTypeMetadata])
def list_connector_types(_user: User = Depends(get_current_user)):
    """All supported connector types with field metadata for dynamic forms (FR1)."""
    return connector_catalog.CONNECTOR_TYPES


@router.get("/types/{type}", response_model=ConnectorTypeMetadata)
def get_connector_type(type: str, _user: User = Depends(get_current_user)):
    """Metadata for a single connector type."""
    return connector_catalog.get_type_or_404(type)


@router.get("/health-summary", response_model=HealthSummary)
def health_summary(db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Aggregate health counts across non-deleted connections (FR5)."""
    return ConnectionService.health_summary(db, owner_email=_owner_scope(user))


@router.get("/deleted", response_model=List[ConnectionResponse])
def list_deleted_connections(db: Session = Depends(get_db),
                             _user: User = Depends(require_role("admin"))):
    """Soft-deleted connections (admin only)."""
    return [_to_response(c) for c in ConnectionService.list_deleted(db)]


# ── CRUD ────────────────────────────────────────────────────────

@router.post("/", response_model=ConnectionResponse, status_code=201)
def create_connection(conn: ConnectionCreate, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Create a new database connector (config validated against the type
    catalog; secret values never appear in the response)."""
    created = ConnectionService.create_connection(
        db, name=conn.name, conn_type=conn.type, config=conn.config,
        environment=conn.environment,
        actor=_actor(user), owner_email=user.email,
    )
    
    # Automatically trigger Databricks ingestion pipeline if it's a source connector
    unity_catalog = None
    if conn.type.lower() != "databricks":
        try:
            from app.services.databricks_ingestion_service import DatabricksIngestionService
            logger.info(f"Automatically setting up Databricks Lakehouse Federation for new connector {created.id}")
            unity_catalog = DatabricksIngestionService.setup_lakehouse_federation(
                source_conn=created,
                actor=_actor(user)
            )
        except Exception as e:
            logger.exception("Failed to publish connector %d to Unity Catalog", created.id)
            unity_catalog = {
                "status": "failed",
                "message": "Connection saved, but publishing it to Unity Catalog failed. Use Retry Unity Catalog after checking workspace permissions.",
            }
            
    return _to_response(created, unity_catalog=unity_catalog)


@router.post("/{id}/federate")
def federate_connection(id: int, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Create or repair a foreign Unity Catalog for a saved source connection."""
    db_conn = ConnectionService.get_connection(db, id, owner_email=_owner_scope(user))
    if db_conn.type.lower() == "databricks":
        raise HTTPException(status_code=400, detail="Databricks connections are already Unity Catalog sources")
    try:
        from app.services.databricks_ingestion_service import DatabricksIngestionService
        return DatabricksIngestionService.setup_lakehouse_federation(
            source_conn=db_conn, actor=_actor(user)
        )
    except Exception as exc:
        logger.exception("Unity Catalog federation retry failed for connector %d", id)
        raise HTTPException(
            status_code=502,
            detail="Could not publish this connection to Unity Catalog. Check CREATE CONNECTION, CREATE CATALOG, and grant permissions.",
        ) from exc


@router.get("/", response_model=List[ConnectionResponse])
def list_connections(db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """List all configured (non-deleted) database connectors owned by the
    caller (or every connection, for admins)."""
    return [_to_response(c) for c in ConnectionService.list_connections(db, owner_email=_owner_scope(user))]


@router.get("/{id}", response_model=ConnectionResponse)
def get_connection(id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Get connection details by ID (404 for soft-deleted or not-yours rows)."""
    return _to_response(ConnectionService.get_connection(db, id, owner_email=_owner_scope(user)))


@router.patch("/{id}/environment", response_model=ConnectionResponse)
def update_connection_environment(
    id: int, body: ConnectionEnvironmentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    """Set a display/filter label; this is explicitly not an isolation boundary."""
    return _to_response(ConnectionService.update_environment(
        db, id, body.environment, actor=_actor(user), owner_email=_owner_scope(user),
    ))


@router.delete("/{id}")
def delete_connection(id: int, confirm: bool = Query(False),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Soft-delete a connector (FR7). If mappings/pipelines depend on it,
    the first call returns a warning; repeat with ?confirm=true to proceed.
    Dependent pipelines are disabled, dependents recorded in the audit trail."""
    db_conn = ConnectionService.get_connection(db, id, owner_email=_owner_scope(user))
    dependents = ConnectionService.get_dependents(db, id)

    if dependents["total"] > 0 and not confirm:
        return {
            "warning": (
                f"This connection is used by {dependents['total']} resource(s). "
                "Deleting it will disable dependent pipelines and break dependent "
                "mappings. Repeat the request with ?confirm=true to proceed."
            ),
            "dependents": dependents,
            "requires_confirm": True,
        }

    ConnectionService.soft_delete_connection(db, id, actor=_actor(user),
                                             dependents=dependents,
                                             owner_email=_owner_scope(user))
    return {
        "status": "deleted",
        "id": id,
        "name": db_conn.name,
        "affected_dependents": dependents["total"],
    }


@router.post("/{id}/restore", response_model=ConnectionResponse)
def restore_connection(id: int, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Restore a soft-deleted connection (409 if its name was reused)."""
    return _to_response(
        ConnectionService.restore_connection(db, id, actor=_actor(user),
                                             owner_email=_owner_scope(user))
    )


@router.delete("/{id}/hard", status_code=204)
def hard_delete_connection(id: int, db: Session = Depends(get_db),
                           user: User = Depends(require_role("admin"))):
    """Permanently delete a connection (admin only; blocked while dependents exist)."""
    ConnectionService.hard_delete_connection(db, id, actor=_actor(user),
                                             owner_email=_owner_scope(user))


# ── Credential vaulting (keeperdb_integration_tasks #5/#6) ─────


@router.post("/migrate-secrets")
def migrate_secrets(db: Session = Depends(get_db),
                    user: User = Depends(require_role("admin"))):
    """One-time, idempotent backfill: move legacy plaintext secrets out of
    the config column into the vault. Explicit and admin-triggered — NEVER a
    side effect of a read (connector_tasks #2's corrected design). Re-running
    is a no-op."""
    from app.services.connection_secrets_service import migrate_plaintext_secrets
    return migrate_plaintext_secrets(db, actor=_actor(user))


@router.post("/{id}/rotate-credentials")
def rotate_credentials(id: int, body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require_role("admin"))):
    """Rotate a connection's credentials via the vault. The response never
    echoes secret values — field names only. Body validated manually so a
    malformed request can't bounce secrets back in a 422 echo."""
    secrets = (body or {}).get("secrets")
    if (not isinstance(secrets, dict) or not secrets
            or not all(isinstance(v, str) and v for v in secrets.values())):
        # Deliberately does NOT echo the received body (connector_tasks #2's
        # 422-echo risk note).
        raise HTTPException(
            status_code=422,
            detail="body must be {\"secrets\": {\"<field>\": \"<value>\"}} with "
                   "non-empty string values (values not echoed)",
        )
    from app.services.connection_secrets_service import rotate_credentials as _rotate
    return _rotate(db, id, secrets, actor=_actor(user))


# ── Test / schema / discovery ──────────────────────────────────

@router.post("/{id}/test", response_model=TestConnectionResponse)
def test_connection(id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Test connectivity with structured diagnostics and a hard timeout
    (FR4). Also updates the stored health status and emits an audit event."""
    db_conn = ConnectionService.get_connection(db, id, owner_email=_owner_scope(user))
    result = SchemaService.test_connection(db_conn)
    status = "connected" if result.success else "failed"

    if result.success:
        health = "healthy"
    elif result.reachable:
        health = "degraded"
    else:
        health = "down"
    ConnectionService.update_health(db, id, health, result.error_message)

    logger.info("[connectors] stage=test id=%d status=%s code=%s latency_ms=%s",
                id, status, result.error_code, result.latency_ms)
    record_audit(db, "connector_tested", actor=_actor(user),
                 connection_id=id, connection_name=db_conn.name,
                 status="success" if result.success else "failure",
                 duration_ms=result.latency_ms,
                 payload={"status": status, "error_code": result.error_code})
    db.commit()

    return TestConnectionResponse(
        id=id,
        name=db_conn.name,
        status=status,
        diagnostics=TestDiagnostics(
            reachable=result.reachable,
            authenticated=result.authenticated,
            database_accessible=result.database_accessible,
            version=result.version,
            latency_ms=result.latency_ms,
        ),
        error=TestErrorDetail(code=result.error_code or "UNKNOWN_ERROR",
                              message=result.error_message or "Connection test failed")
        if not result.success else None,
    )


def _databricks_live_schema(db: Session, conn: DBConnection, snapshot, *, refresh: bool = False) -> Optional[dict]:
    """Schema of a Databricks connection from the live Unity Catalog (REST), cached on the connection's own snapshot.

    Serves the snapshot while it is younger than DATABRICKS_SCHEMA_TTL_SECONDS, otherwise rescans. Returns None when
    the scan is unavailable or finds nothing, so the caller falls back to the legacy snapshot / connector path.
    """
    from app.models.schema_snapshot import SchemaSnapshot

    ttl = settings.DATABRICKS_SCHEMA_TTL_SECONDS
    if not refresh and snapshot is not None and snapshot.captured_at:
        captured = snapshot.captured_at
        # Postgres returns tz-aware timestamps, SQLite naive ones (stored as UTC either way)
        now = datetime.now(timezone.utc) if captured.tzinfo else datetime.utcnow()
        if (now - captured).total_seconds() < ttl:
            return snapshot.schema_json
    try:
        from app.services.databricks_ingestion_service import _get_workspace_client
        from app.services.databricks_schema_scan import scan_unity_catalog
        scanned = scan_unity_catalog(_get_workspace_client(None))
    except Exception as exc:
        logger.warning("[connectors] live Unity Catalog scan unavailable for connector %d: %s", conn.id, exc)
        return None
    if not scanned:
        return None
    digest = hashlib.sha256(json.dumps(scanned, sort_keys=True, default=str).encode()).hexdigest()
    if snapshot is None:
        db.add(SchemaSnapshot(connection_id=conn.id, connection_name=conn.name, schema_hash=digest, schema_json=scanned))
    else:
        snapshot.schema_json, snapshot.schema_hash = scanned, digest
    db.commit()
    return scanned


@router.get("/{id}/schema")
def get_schema(id: int, db: Session = Depends(get_db),
               refresh: bool = Query(False),
               user: User = Depends(get_current_user)):
    """Extract full structural schema metadata from the connector."""
    from app.models.schema_snapshot import SchemaSnapshot
    db_conn = ConnectionService.get_connection(db, id, owner_email=_owner_scope(user))
    try:
        # Check if we have a cached schema snapshot first to avoid a very slow live scan
        snapshot = (
            db.query(SchemaSnapshot)
            .filter(SchemaSnapshot.connection_id == id)
            .order_by(SchemaSnapshot.captured_at.desc())
            .first()
        )

        # Databricks: show the live catalogs of the current workspace instead of another connection's old snapshot
        if db_conn.type.lower() == "databricks":
            live = _databricks_live_schema(db, db_conn, snapshot, refresh=refresh)
            if live is not None:
                return {"id": id, "name": db_conn.name, "schema": live}

        # If no snapshot for THIS connector, try to reuse snapshot from any other
        # Databricks connector in the workspace — they all point to the same Unity Catalog.
        if not snapshot and db_conn.type.lower() == "databricks":
            from app.models.connection import DBConnection as _DBC
            other_db_conn_ids = (
                db.query(_DBC.id)
                .filter(
                    _DBC.type == "databricks",
                    _DBC.is_deleted == False,  # noqa: E712
                    _DBC.id != id,
                )
                .all()
            )
            other_ids = [r[0] for r in other_db_conn_ids]
            if other_ids:
                snapshot = (
                    db.query(SchemaSnapshot)
                    .filter(SchemaSnapshot.connection_id.in_(other_ids))
                    .order_by(SchemaSnapshot.captured_at.desc())
                    .first()
                )
                if snapshot:
                    logger.info(
                        "Schema cache: reusing snapshot from connector %d for connector %d",
                        snapshot.connection_id, id
                    )

        if snapshot:
            schema_data = snapshot.schema_json
        else:
            # Fallback to live scan only if absolutely no snapshot exists anywhere
            schema_data = SchemaService.get_full_schema(db_conn)
            # Save the result as a snapshot so future clicks are instant
            import json, hashlib
            normalized = json.dumps(schema_data, sort_keys=True, default=str)
            new_snapshot = SchemaSnapshot(
                connection_id=id,
                connection_name=db_conn.name,
                schema_hash=hashlib.sha256(normalized.encode()).hexdigest(),
                schema_json=schema_data,
            )
            db.add(new_snapshot)
            db.commit()
            
        return {"id": id, "name": db_conn.name, "schema": schema_data}
    except Exception as e:
        logger.error("Schema extraction failed for connector %d: %s", id, e)
        raise HTTPException(status_code=500, detail=f"Schema extraction failed: {str(e)}")


@router.post("/{id}/discover")
def discover_schema(id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Trigger schema discovery (FR8): snapshot the current schema for
    drift detection and hand off to Schema Intel's catalog scan."""
    import hashlib
    import json

    db_conn = ConnectionService.get_connection(db, id, owner_email=_owner_scope(user))
    actor = _actor(user)

    try:
        schema_data = SchemaService.get_full_schema(db_conn)
    except Exception as e:
        logger.error("[connectors] stage=discover id=%d failed: %s", id, e)
        record_audit(db, "discovery_failed", actor=actor,
                     connection_id=id, connection_name=db_conn.name,
                     status="failure", payload={"error": str(e)})
        db.commit()
        raise HTTPException(status_code=500, detail=f"Schema discovery failed: {str(e)}")

    from app.models.schema_snapshot import SchemaSnapshot
    normalized = json.dumps(schema_data, sort_keys=True, default=str)
    snapshot = SchemaSnapshot(
        connection_id=id,
        connection_name=db_conn.name,
        schema_hash=hashlib.sha256(normalized.encode()).hexdigest(),
        schema_json=schema_data,
    )
    db.add(snapshot)
    db.flush()

    # Handoff to Schema Intel's catalog (best-effort — discovery still
    # succeeds if the catalog scan fails). Note scan_connection commits,
    # which also persists the snapshot staged above.
    catalog_scan = None
    try:
        from app.services.schema_catalog_service import SchemaCatalogService
        catalog_scan = SchemaCatalogService.scan_connection(db, id, actor=actor)
    except ImportError:
        logger.info("[connectors] Schema Intel catalog not available — skipping handoff")
    except Exception as e:
        logger.warning("[connectors] Schema Intel catalog scan failed (non-fatal): %s", e)

    logger.info("[connectors] stage=discover id=%d tables=%d catalog_handoff=%s",
                id, len(schema_data), bool(catalog_scan))
    record_audit(db, "discovery_completed", actor=actor,
                 connection_id=id, connection_name=db_conn.name,
                 payload={"tables": len(schema_data), "snapshot_id": snapshot.id,
                          "catalog_handoff": bool(catalog_scan)})
    db.commit()

    return {
        "id": id,
        "name": db_conn.name,
        "tables": len(schema_data),
        "snapshot_id": snapshot.id,
        "catalog_scan": catalog_scan,
        "tables_discovered": sorted(schema_data.keys()),
    }
