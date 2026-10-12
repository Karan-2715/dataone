"""SecretManager abstraction (keeperdb_integration_tasks #1, implementing
the interface designed in requirements-specs/connector_tasks/02).

Backend-agnostic vault interface for DB-connector credentials. The ONLY
place that branches on backend choice is get_secret_manager() — nothing
outside a concrete backend module may import backend-specific types
(portability NFR: a future third backend must be a pure swap-in).

Decision record (v5 task #2, resolved 2026-07-14 by repo owner): BOTH
backends ship — self-hosted AES-256-GCM as the working default (zero
external infrastructure, fully testable locally) and Keeper Secrets Manager
behind SECRET_MANAGER_BACKEND=keeper. Production posture stays revisitable
without touching connector code.

Ref formats (opaque strings, round-trip through DBConnection.secrets_ref):
    aes256://<connection_secret_row_id>
    keeper://<record_uid>

SECURITY: no method in this module or any backend may ever log, echo, or
serialize a secret VALUE. Refs, field names, and connection ids only.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)


class SecretManagerError(Exception):
    """Base error for vault operations. Messages never contain secret values."""


class SecretManagerNotConfigured(SecretManagerError):
    """The selected backend is missing its required configuration."""


class SecretManager(ABC):
    @abstractmethod
    def store(self, connection_id: int, secrets: Dict[str, Any],
              db: Optional[Session] = None) -> str:
        """Store secret values; returns a secrets_ref string. `db` (when the
        backend persists locally) joins the caller's transaction so a failed
        connection create doesn't orphan a vault row."""

    @abstractmethod
    def retrieve(self, secrets_ref: str,
                 db: Optional[Session] = None) -> Dict[str, Any]:
        """Retrieve secret values by ref. Server-side only — never exposed
        to a client."""

    @abstractmethod
    def rotate(self, secrets_ref: str, new_secrets: Dict[str, Any],
               db: Optional[Session] = None) -> str:
        """Update secrets; may return a new ref (callers must handle a
        changed ref even though some backends keep it stable)."""

    @abstractmethod
    def delete(self, secrets_ref: str, db: Optional[Session] = None) -> None:
        """Remove secrets from the vault (hard delete only — soft-deleted
        connections retain their ref so restore keeps working)."""


def secret_manager_enabled() -> bool:
    """Whether vaulting is actively configured. False = legacy behavior
    (secrets stay in config; response-layer redaction still applies) —
    existing deployments don't break the moment this code ships.

    An UNKNOWN backend raises rather than returning False: a typo'd backend
    name must not silently fall through to legacy plaintext storage while the
    operator believes vaulting is on (a silent security downgrade)."""
    backend = settings.SECRET_MANAGER_BACKEND
    if backend == "keeper":
        return bool(settings.KSM_CONFIG_PATH)
    if backend == "aes256":
        return bool(settings.SECRETS_ENCRYPTION_KEY)
    raise SecretManagerNotConfigured(
        f"unknown SECRET_MANAGER_BACKEND '{backend}' (expected 'aes256' or 'keeper')"
    )


def get_secret_manager() -> SecretManager:
    """The single backend-choice branch point."""
    backend = settings.SECRET_MANAGER_BACKEND
    if backend == "keeper":
        from app.services.keeper_secrets_manager_backend import (
            KeeperSecretsManagerBackend,
        )
        return KeeperSecretsManagerBackend()
    if backend == "aes256":
        from app.services.aes_gcm_secret_manager import AesGcmSecretManager
        return AesGcmSecretManager()
    raise SecretManagerNotConfigured(
        f"unknown SECRET_MANAGER_BACKEND '{backend}' (expected 'aes256' or 'keeper')"
    )
