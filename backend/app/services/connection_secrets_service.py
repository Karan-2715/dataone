"""Connector-credential vaulting glue (keeperdb_integration_tasks #4/#5/#6/#8).

Sits between ConnectionService/get_connector and the SecretManager
abstraction: extracts known secret fields at write time, resolves them back
at connector-construction time, owns the one-time backfill migration and
the rotation flow, and emits the vault audit events.

Audit batching (task #8): `retrieve()` runs on every connector use — during
profiling that's once per column — so secret_retrieve is audited at most
once per connection per _AUDIT_TTL_SECONDS window (a real, explainable
batching mechanism), not per call. store/rotate/delete are rare and audited
every time. NO audit payload ever contains a secret value — field names,
refs, and connection ids only.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.connection import DBConnection
from app.services import connector_catalog
from app.services.secret_manager import (
    SecretManagerError,
    get_secret_manager,
    secret_manager_enabled,
)

logger = logging.getLogger(__name__)

_AUDIT_TTL_SECONDS = 60
_retrieve_audit_cache: TTLCache = TTLCache(maxsize=512, ttl=_AUDIT_TTL_SECONDS)
# cachetools.TTLCache is NOT thread-safe; resolve_connection_config runs from
# concurrent request threads and Celery workers, so every touch of the cache
# is serialized under this lock. (The lock guards only the fast in-memory
# check/set — the audit DB write happens outside it.)
_retrieve_audit_lock = threading.Lock()

_warned_disabled = False


def extract_secret_fields(conn_type: str,
                          config: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Split a config into (non_secret_config, secrets) using the same
    per-type secret-field mapping redact_config already trusts."""
    secret_keys = (connector_catalog.secret_fields_for_type(conn_type)
                   | connector_catalog._FALLBACK_SECRET_KEYS)
    clean: Dict[str, Any] = {}
    secrets: Dict[str, Any] = {}
    for key, value in (config or {}).items():
        if key in secret_keys and value is not None:
            secrets[key] = value
        else:
            clean[key] = value
    return clean, secrets


def store_secrets_for_new_connection(db: Session, conn: DBConnection,
                                     secrets: Dict[str, Any], *, actor: str) -> None:
    """Vault a new connection's secrets inside the caller's transaction and
    stage the audit event. No-op when there's nothing to store."""
    if not secrets:
        return
    manager = get_secret_manager()
    conn.secrets_ref = manager.store(conn.id, secrets, db=db)
    _audit(db, "secret_store", actor=actor, connection=conn,
           metadata={"fields": sorted(secrets), "backend": _backend_name()})


def vaulting_active() -> bool:
    """Enabled AND configured. Logs the legacy-mode warning exactly once."""
    global _warned_disabled
    if secret_manager_enabled():
        return True
    if not _warned_disabled:
        logger.warning(
            "[secrets] secret manager not configured (SECRET_MANAGER_BACKEND=%s) — "
            "connector credentials stay in the config column (legacy mode); "
            "responses remain redacted. Set SECRETS_ENCRYPTION_KEY (aes256) or "
            "KSM_CONFIG_PATH (keeper) to enable vaulting.",
            _settings().SECRET_MANAGER_BACKEND,
        )
        _warned_disabled = True
    return False


def resolve_connection_config(connection: DBConnection) -> Dict[str, Any]:
    """Merged config (non-secret + vault secrets) for connector construction.
    Server-side only. A vault outage fails HERE, with a clear error, so only
    credential-dependent operations break — metadata reads never call this."""
    config = dict(connection.config or {})
    if not connection.secrets_ref:
        return config
    manager = get_secret_manager()
    secrets = manager.retrieve(connection.secrets_ref)
    config.update(secrets)
    _audit_retrieve_batched(connection)
    return config


def _audit_retrieve_batched(connection: DBConnection) -> None:
    # The whole body is best-effort: auditing must NEVER break credential
    # resolution, so both the (thread-unsafe) cache access and the DB write are
    # inside the try. A concurrent-cache RuntimeError/KeyError here used to
    # propagate out of resolve_connection_config and fail an otherwise-healthy
    # connector build.
    try:
        with _retrieve_audit_lock:
            if connection.id in _retrieve_audit_cache:
                return
            _retrieve_audit_cache[connection.id] = True
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            _audit(db, "secret_retrieve", actor="system", connection=connection,
                   metadata={"backend": _backend_name(),
                             "batched_window_seconds": _AUDIT_TTL_SECONDS})
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # audit must never block credential resolution
        logger.warning("[secrets] retrieve audit failed (non-fatal): %s", exc)


# ── Backfill migration (task #5 — explicit, idempotent, admin-triggered) ──


def migrate_plaintext_secrets(db: Session, *, actor: str) -> Dict[str, Any]:
    """Move every legacy plaintext secret out of `config` into the vault.
    Explicit (admin endpoint) and idempotent: rows with a secrets_ref are
    skipped; re-running is a no-op. NEVER triggered by a GET."""
    if not secret_manager_enabled():
        raise HTTPException(
            status_code=409,
            detail="secret manager is not configured — set SECRETS_ENCRYPTION_KEY "
                   "(aes256) or KSM_CONFIG_PATH (keeper) before migrating",
        )
    manager = get_secret_manager()
    migrated: List[int] = []
    skipped: List[int] = []
    for conn in (
        db.query(DBConnection)
        .filter(DBConnection.is_deleted == False)  # noqa: E712
        .order_by(DBConnection.id)
        .all()
    ):
        if conn.secrets_ref:
            skipped.append(conn.id)
            continue
        clean, secrets = extract_secret_fields(conn.type, conn.config)
        if not secrets:
            skipped.append(conn.id)
            continue
        conn.secrets_ref = manager.store(conn.id, secrets, db=db)
        conn.config = clean
        _audit(db, "secret_store", actor=actor, connection=conn,
               metadata={"fields": sorted(secrets), "backend": _backend_name(),
                         "via": "backfill_migration"})
        migrated.append(conn.id)
        logger.info("[secrets] migrated connection_id=%d fields=%s",
                    conn.id, sorted(secrets))
    db.commit()
    return {"migrated": migrated, "skipped": skipped,
            "migrated_count": len(migrated), "skipped_count": len(skipped)}


# ── Rotation (task #6 — resolves connector_tasks #8's blocked chain) ─────


def rotate_credentials(db: Session, connection_id: int,
                       new_secrets: Dict[str, Any], *, actor: str) -> Dict[str, Any]:
    from app.services.connection_service import ConnectionService

    conn = ConnectionService.get_connection(db, connection_id)
    allowed = (connector_catalog.secret_fields_for_type(conn.type)
               | connector_catalog._FALLBACK_SECRET_KEYS)
    if not connector_catalog.secret_fields_for_type(conn.type):
        raise HTTPException(
            status_code=422,
            detail=f"connector type '{conn.type}' has no credential fields to rotate",
        )
    unknown = set(new_secrets) - allowed
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"not credential fields for type '{conn.type}': {sorted(unknown)}",
        )
    if not secret_manager_enabled():
        raise HTTPException(
            status_code=409,
            detail="secret manager is not configured — rotation requires the vault",
        )

    manager = get_secret_manager()
    if conn.secrets_ref:
        # Rotation requests may contain only one of several credential fields.
        # Fail closed when the existing record cannot be read: treating an
        # outage/lost key as an empty record would overwrite the vault entry
        # with the partial request and silently discard the other credentials.
        existing = manager.retrieve(conn.secrets_ref, db=db)
        merged = {**existing, **new_secrets}
        conn.secrets_ref = manager.rotate(conn.secrets_ref, merged, db=db)
    else:
        # Legacy row: rotation doubles as its migration (same function the
        # backfill uses — one code path, per connector_tasks #2's corrected note).
        clean, current = extract_secret_fields(conn.type, conn.config)
        merged = {**current, **new_secrets}
        conn.secrets_ref = manager.store(conn.id, merged, db=db)
        conn.config = clean

    conn.updated_by = actor
    _audit(db, "secret_rotate", actor=actor, connection=conn,
           metadata={"fields": sorted(new_secrets), "backend": _backend_name()})
    db.commit()
    return {"connection_id": conn.id, "rotated_fields": sorted(new_secrets)}


def delete_secrets_for_connection(db: Session, conn: DBConnection, *,
                                  actor: str) -> None:
    """Hard-delete hook: remove the vault entry. Soft delete keeps it so
    restore keeps working."""
    if not conn.secrets_ref:
        return
    try:
        get_secret_manager().delete(conn.secrets_ref, db=db)
    except SecretManagerError as exc:
        logger.warning("[secrets] vault delete failed for connection %d: %s",
                       conn.id, exc)
        return
    _audit(db, "secret_delete", actor=actor, connection=conn,
           metadata={"backend": _backend_name()})


# ── helpers ───────────────────────────────────────────────────────────────


def _audit(db: Session, event_type: str, *, actor: str,
           connection: DBConnection, metadata: Dict[str, Any]) -> None:
    from app.services.audit_helper import emit_audit_event
    emit_audit_event(
        db, event_type=f"secrets.{event_type}", actor=actor,
        module="secrets", target_type="connection",
        target_id=connection.id, target_name=connection.name,
        summary=f"{event_type} for connection {connection.name}",
        outcome="success", metadata=metadata,
    )


def _backend_name() -> str:
    return _settings().SECRET_MANAGER_BACKEND


def _settings():
    from app.core.config import settings
    return settings
