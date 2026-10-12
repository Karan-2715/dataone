"""Connection CRUD + health + dependency service (connector_tasks #1/#5/#7).

Routers stay thin; every rule about soft-delete visibility, name
uniqueness among active rows, health transitions, and cross-model
dependency checks lives here.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.connection import DBConnection
from app.services import connector_catalog
from app.services.audit_helper import record_audit

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")


class ConnectionService:
    # ── CRUD ────────────────────────────────────────────────────

    @staticmethod
    def create_connection(db: Session, *, name: str, conn_type: str,
                          config: Dict[str, Any], environment: str = "dev",
                          actor: str, owner_email: str) -> DBConnection:
        if not _NAME_RE.match(name or ""):
            raise HTTPException(
                status_code=422,
                detail="Name must be 1–100 alphanumeric/underscore/hyphen characters",
            )
        cleaned = connector_catalog.validate_config(conn_type, config)

        existing = (
            db.query(DBConnection)
            .filter(DBConnection.name == name,
                    DBConnection.owner_email == owner_email,
                    DBConnection.is_deleted == False)  # noqa: E712
            .first()
        )
        if existing:
            raise HTTPException(status_code=409,
                                detail=f"A connector named '{name}' already exists")

        # Vaulting (keeperdb_integration_tasks #4): secret fields never land
        # in the config column when the secret manager is configured; legacy
        # mode (unconfigured) keeps them there, still redacted at the API.
        from app.services import connection_secrets_service as secrets_svc
        secrets: Dict[str, Any] = {}
        if secrets_svc.vaulting_active():
            cleaned, secrets = secrets_svc.extract_secret_fields(conn_type, cleaned)

        conn = DBConnection(name=name, type=conn_type, config=cleaned,
                            environment=environment, owner_email=owner_email,
                            created_by=actor, updated_by=actor)
        db.add(conn)
        db.flush()
        if secrets:
            secrets_svc.store_secrets_for_new_connection(db, conn, secrets, actor=actor)
        logger.info("[connectors] stage=create name=%s type=%s id=%d vaulted=%s",
                    conn.name, conn.type, conn.id, bool(secrets))
        record_audit(db, "connector_created", actor=actor,
                     connection_id=conn.id, connection_name=conn.name,
                     payload={"type": conn.type})
        db.commit()
        db.refresh(conn)
        return conn

    @staticmethod
    def update_environment(db: Session, id: int, environment: str, *, actor: str,
                           owner_email: Optional[str] = None) -> DBConnection:
        conn = ConnectionService.get_connection(db, id, owner_email=owner_email)
        before = conn.environment
        conn.environment = environment
        conn.updated_by = actor
        record_audit(
            db, "connector_environment_changed", actor=actor,
            connection_id=conn.id, connection_name=conn.name,
            payload={"before": before, "after": environment, "isolation": False},
        )
        db.commit()
        db.refresh(conn)
        return conn

    @staticmethod
    def get_connection(db: Session, id: int, include_deleted: bool = False,
                       owner_email: Optional[str] = None) -> DBConnection:
        """`owner_email=None` means unscoped (admin bypass — see
        connectors.py's `_owner_scope`); a real value 404s rows owned by
        someone else rather than leaking their existence via 403."""
        q = db.query(DBConnection).filter(DBConnection.id == id)
        if not include_deleted:
            q = q.filter(DBConnection.is_deleted == False)  # noqa: E712
        if owner_email is not None:
            q = q.filter(DBConnection.owner_email == owner_email)
        conn = q.first()
        if not conn:
            raise HTTPException(status_code=404, detail="Connection not found")
        return conn

    @staticmethod
    def list_connections(db: Session, include_deleted: bool = False,
                         owner_email: Optional[str] = None) -> List[DBConnection]:
        q = db.query(DBConnection)
        if not include_deleted:
            q = q.filter(DBConnection.is_deleted == False)  # noqa: E712
        if owner_email is not None:
            q = q.filter(DBConnection.owner_email == owner_email)
        return q.order_by(DBConnection.id).all()

    @staticmethod
    def list_deleted(db: Session) -> List[DBConnection]:
        return (
            db.query(DBConnection)
            .filter(DBConnection.is_deleted == True)  # noqa: E712
            .order_by(DBConnection.deleted_at.desc())
            .all()
        )

    # ── Health (connector_tasks #4/#5) ──────────────────────────

    @staticmethod
    def update_health(db: Session, id: int, status: str,
                      error: Optional[str] = None) -> None:
        """Record the outcome of a test/health check. Stages only — the
        caller owns the commit (matches record_audit's contract)."""
        conn = db.query(DBConnection).filter(DBConnection.id == id).first()
        if not conn:
            logger.warning("[connectors] stage=update_health id=%s missing", id)
            return
        conn.health_status = status
        conn.last_tested_at = datetime.now(timezone.utc)
        conn.last_test_error = error

    @staticmethod
    def health_summary(db: Session, owner_email: Optional[str] = None) -> Dict[str, Any]:
        conns = ConnectionService.list_connections(db, owner_email=owner_email)
        counts = {"healthy": 0, "degraded": 0, "down": 0, "unknown": 0}
        last = None
        for c in conns:
            counts[c.health_status if c.health_status in counts else "unknown"] += 1
            if c.last_tested_at and (last is None or c.last_tested_at > last):
                last = c.last_tested_at
        return {"total": len(conns), **counts, "last_tested_at": last}

    # ── Soft delete / restore (connector_tasks #7) ──────────────

    @staticmethod
    def get_dependents(db: Session, connection_id: int) -> Dict[str, Any]:
        """Resources that FK this connection. Each model checked
        independently and best-effort so a missing/renamed model degrades
        to a warning instead of blocking deletion."""
        dependents: Dict[str, Any] = {"mappings": [], "pipelines": [], "total": 0}

        try:
            from app.models.mapping import Mapping
            mappings = (
                db.query(Mapping)
                .filter(
                    or_(Mapping.source_id == connection_id,
                        Mapping.target_id == connection_id),
                    Mapping.deleted_at.is_(None),
                )
                .all()
            )
            dependents["mappings"] = [
                {"id": m.id, "name": m.name,
                 "role": "source" if m.source_id == connection_id else "target"}
                for m in mappings
            ]
        except Exception as e:
            logger.warning("[connectors] could not check Mapping dependents: %s", e)

        try:
            from app.models.pipeline import Pipeline
            pipelines = (
                db.query(Pipeline)
                .filter(
                    or_(Pipeline.source_connection_id == connection_id,
                        Pipeline.target_connection_id == connection_id),
                )
                .all()
            )
            dependents["pipelines"] = [
                {"id": p.id, "name": p.name, "enabled": bool(p.enabled),
                 "role": "source" if p.source_connection_id == connection_id else "target"}
                for p in pipelines
            ]
        except Exception as e:
            logger.warning("[connectors] could not check Pipeline dependents: %s", e)

        dependents["total"] = len(dependents["mappings"]) + len(dependents["pipelines"])
        return dependents

    @staticmethod
    def soft_delete_connection(db: Session, id: int, *, actor: str,
                               dependents: Dict[str, Any],
                               owner_email: Optional[str] = None) -> DBConnection:
        """Soft-delete + flag dependents. Pipelines that reference the
        connection are disabled (they cannot run without it); Mapping has
        no status field for this, so affected mappings are only recorded
        in the audit payload — honest best-effort per the task spec."""
        conn = ConnectionService.get_connection(db, id, owner_email=owner_email)
        conn.is_deleted = True
        conn.deleted_at = datetime.now(timezone.utc)
        conn.updated_by = actor

        disabled_pipelines = []
        if dependents["pipelines"]:
            try:
                from app.models.pipeline import Pipeline
                for dep in dependents["pipelines"]:
                    p = db.query(Pipeline).filter(Pipeline.id == dep["id"]).first()
                    if p and p.enabled:
                        p.enabled = False
                        disabled_pipelines.append(p.id)
            except Exception as e:
                logger.warning("[connectors] could not disable dependent pipelines: %s", e)

        logger.info("[connectors] stage=soft_delete id=%d dependents=%d disabled_pipelines=%s",
                    id, dependents["total"], disabled_pipelines)
        record_audit(db, "connector_deleted", actor=actor,
                     connection_id=conn.id, connection_name=conn.name,
                     payload={"soft_delete": True,
                              "dependents_count": dependents["total"],
                              "affected_mappings": [m["id"] for m in dependents["mappings"]],
                              "disabled_pipelines": disabled_pipelines})
        db.commit()
        db.refresh(conn)
        return conn

    @staticmethod
    def restore_connection(db: Session, id: int, *, actor: str,
                           owner_email: Optional[str] = None) -> DBConnection:
        q = db.query(DBConnection).filter(
            DBConnection.id == id, DBConnection.is_deleted == True,  # noqa: E712
        )
        if owner_email is not None:
            q = q.filter(DBConnection.owner_email == owner_email)
        conn = q.first()
        if not conn:
            raise HTTPException(status_code=404, detail="Deleted connection not found")

        clash = (
            db.query(DBConnection)
            .filter(DBConnection.name == conn.name,
                    DBConnection.owner_email == conn.owner_email,
                    DBConnection.is_deleted == False,  # noqa: E712
                    DBConnection.id != id)
            .first()
        )
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"An active connector named '{conn.name}' already exists — "
                       "rename it before restoring",
            )

        conn.is_deleted = False
        conn.deleted_at = None
        conn.updated_by = actor
        logger.info("[connectors] stage=restore id=%d name=%s", id, conn.name)
        record_audit(db, "connector_restored", actor=actor,
                     connection_id=conn.id, connection_name=conn.name)
        db.commit()
        db.refresh(conn)
        return conn

    @staticmethod
    def hard_delete_connection(db: Session, id: int, *, actor: str,
                               owner_email: Optional[str] = None) -> None:
        """Permanent removal (admin only, connector_tasks #7). Blocked while
        active dependents still reference the row."""
        conn = ConnectionService.get_connection(db, id, include_deleted=True,
                                                owner_email=owner_email)
        dependents = ConnectionService.get_dependents(db, id)
        if dependents["total"] > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Connection has {dependents['total']} dependent resource(s); "
                       "remove them before hard-deleting",
            )
        # Vault cleanup on hard delete only — soft-deleted connections keep
        # their secrets_ref so restore keeps working (keeperdb tasks #4).
        from app.services import connection_secrets_service as secrets_svc
        secrets_svc.delete_secrets_for_connection(db, conn, actor=actor)
        logger.info("[connectors] stage=hard_delete id=%d name=%s", id, conn.name)
        record_audit(db, "connector_hard_deleted", actor=actor,
                     connection_id=conn.id, connection_name=conn.name)
        db.delete(conn)
        db.commit()
