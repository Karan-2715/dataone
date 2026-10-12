"""Keeper Secrets Manager backend (keeperdb_integration_tasks #3).

Wraps `keeper-secrets-manager-core` (MIT, PyPI) behind the SecretManager
interface. Zero-knowledge vault: secrets decrypt client-side from the
KSM_CONFIG_PATH config file (produced by Keeper's one-time-token bootstrap —
never a literal token in env/code). Centralized rotation is the draw:
rotate once in the vault, every connector re-fetches the new value.

Wrapped in a named CircuitBreaker (same pattern as Ollama/ACI) so a Keeper
outage degrades gracefully: connection METADATA stays readable — only
credential-dependent operations fail, with a clear error.

The SDK import is deferred so this module imports cleanly without the
package installed; tests stub the client at the _get_client boundary.

SECURITY: no secret value is ever logged — record UIDs and field names only.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpen  # noqa: F401 (re-export)
from app.core.config import settings
from app.services.secret_manager import (
    SecretManager,
    SecretManagerError,
    SecretManagerNotConfigured,
)

logger = logging.getLogger(__name__)

keeper_circuit = CircuitBreaker("keeper", failure_threshold=5, reset_timeout=30.0)

_REF_PREFIX = "keeper://"
_MAX_RETRIES = 2


class KeeperSecretsManagerBackend(SecretManager):

    def __init__(self) -> None:
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not settings.KSM_CONFIG_PATH:
            raise SecretManagerNotConfigured(
                "the keeper secret backend is not configured (KSM_CONFIG_PATH "
                "is unset) — run Keeper's one-time-token bootstrap and mount "
                "the resulting config file")
        from keeper_secrets_manager_core import SecretsManager
        from keeper_secrets_manager_core.storage import FileKeyValueStorage

        self._client = SecretsManager(
            config=FileKeyValueStorage(settings.KSM_CONFIG_PATH))
        return self._client

    def _call(self, op_name: str, fn) -> Any:
        """Breaker-guarded + retried, mirroring aci_client_service."""
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                result = keeper_circuit.call(fn)
                logger.info("[keeper] op=%s outcome=success attempt=%d",
                            op_name, attempt + 1)
                return result
            except CircuitBreakerOpen:
                logger.warning("[keeper] op=%s circuit open — failing fast", op_name)
                raise
            except SecretManagerNotConfigured:
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning("[keeper] op=%s failed (attempt %d/%d): %s",
                               op_name, attempt + 1, _MAX_RETRIES + 1,
                               type(exc).__name__)
                if attempt < _MAX_RETRIES:
                    time.sleep(2 ** attempt)
        raise SecretManagerError(f"keeper {op_name} failed after retries") from last_exc

    @staticmethod
    def _record_uid(secrets_ref: str) -> str:
        if not secrets_ref.startswith(_REF_PREFIX):
            raise SecretManagerError(f"not a keeper ref: {secrets_ref}")
        return secrets_ref[len(_REF_PREFIX):]

    # ── SecretManager interface (db param unused — KSM is external) ───────

    def store(self, connection_id: int, secrets: Dict[str, Any],
              db: Optional[Session] = None) -> str:
        # Resolve the client OUTSIDE the breaker: an unset KSM_CONFIG_PATH is a
        # configuration state, not a Keeper outage, and must not count as a
        # breaker failure.
        client = self._get_client()

        def _do():
            return client.create_secret(
                folder_uid=settings.KSM_FOLDER_UID,
                record_data=self._record_data(f"dataplane-connection-{connection_id}", secrets),
            )

        uid = self._call("store", _do)
        logger.info("[keeper] stage=store connection_id=%d record_uid=%s",
                    connection_id, uid)
        return f"{_REF_PREFIX}{uid}"

    def retrieve(self, secrets_ref: str, db: Optional[Session] = None) -> Dict[str, Any]:
        uid = self._record_uid(secrets_ref)
        client = self._get_client()
        # The network fetch is breaker-guarded; the "no record" decision is
        # made AFTER it returns successfully. A missing/moved record is a
        # benign logical error, not an outage — raising it inside the breaker
        # (as before) let a few stale refs trip the circuit and take down
        # credential resolution for every healthy record.
        records = self._call("retrieve", lambda: client.get_secrets(uids=[uid]))
        if not records:
            raise SecretManagerError(f"no keeper record for ref {secrets_ref}")
        record = records[0]
        out: Dict[str, Any] = {}
        for field in (record.dict.get("custom") or []):
            label = field.get("label") or field.get("type")
            values = field.get("value") or []
            if label and values:
                out[label] = values[0]
        password = record.field("password", single=True)
        if password:
            out.setdefault("password", password)
        return out

    def rotate(self, secrets_ref: str, new_secrets: Dict[str, Any],
               db: Optional[Session] = None) -> str:
        uid = self._record_uid(secrets_ref)
        client = self._get_client()
        records = self._call("rotate_fetch", lambda: client.get_secrets(uids=[uid]))
        if not records:
            raise SecretManagerError(f"no keeper record for ref {secrets_ref}")
        record = records[0]
        for key, value in new_secrets.items():
            if key == "password":
                record.set_standard_field_value("password", value)
            else:
                record.set_custom_field_value(key, value)
        self._call("rotate_save", lambda: client.save(record))
        logger.info("[keeper] stage=rotate record_uid=%s", uid)
        return secrets_ref  # record UID is stable across rotation

    def delete(self, secrets_ref: str, db: Optional[Session] = None) -> None:
        uid = self._record_uid(secrets_ref)
        client = self._get_client()
        self._call("delete", lambda: client.delete_secret(record_uids=[uid]))
        logger.info("[keeper] stage=delete record_uid=%s", uid)

    @staticmethod
    def _record_data(title: str, secrets: Dict[str, Any]) -> Any:
        """Build a KSM login-record payload: `password` as the standard
        field, everything else as custom fields."""
        from keeper_secrets_manager_core.dto.dtos import RecordCreate, RecordField

        record = RecordCreate(record_type="login", title=title)
        fields = []
        if "password" in secrets:
            fields.append(RecordField(field_type="password",
                                      value=[secrets["password"]]))
        record.fields = fields
        record.custom = [
            {"type": "text", "label": key, "value": [value]}
            for key, value in secrets.items() if key != "password"
        ]
        return record
