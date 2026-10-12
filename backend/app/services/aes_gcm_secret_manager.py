"""AES-256-GCM SecretManager backend (keeperdb_integration_tasks #3 —
"Implementation #1" from requirements-specs/connector_tasks/02).

Envelope encryption with zero external infrastructure: a base64 32-byte key
from SECRETS_ENCRYPTION_KEY, random 12-byte nonce per encryption, ciphertext
in the connection_secrets table. Uses the `cryptography` library (already a
dependency) — never a hand-rolled cipher.

Key rotation: `key_id` records which key encrypted each row (first 8 hex
chars of SHA-256(key) — an identifier, NOT key material). During a key
change, SECRETS_ENCRYPTION_KEY_PREVIOUS lets reads try the old key while
rotate() re-encrypts with the new one.

SECURITY: nothing here logs, echoes, or serializes a plaintext secret.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.connection_secret import ConnectionSecret
from app.services.secret_manager import (
    SecretManager,
    SecretManagerError,
    SecretManagerNotConfigured,
)

logger = logging.getLogger(__name__)

_REF_PREFIX = "aes256://"
_NONCE_BYTES = 12


def _load_key(raw: Optional[str]) -> Optional[bytes]:
    if not raw:
        return None
    try:
        key = base64.b64decode(raw)
    except Exception as exc:
        raise SecretManagerNotConfigured(
            "SECRETS_ENCRYPTION_KEY is not valid base64") from exc
    if len(key) != 32:
        raise SecretManagerNotConfigured(
            f"SECRETS_ENCRYPTION_KEY must decode to 32 bytes (got {len(key)})")
    return key


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


@contextmanager
def _session(db: Optional[Session]):
    """Use the caller's session when given (joins its transaction — a failed
    connection create won't orphan a vault row); otherwise a short-lived one."""
    if db is not None:
        yield db, False
        return
    from app.core.database import SessionLocal
    own = SessionLocal()
    try:
        yield own, True
    finally:
        own.close()


class AesGcmSecretManager(SecretManager):

    def _keys(self) -> Dict[str, bytes]:
        """{key_id: key}, current first. Previous key included only during a
        rotation window (SECRETS_ENCRYPTION_KEY_PREVIOUS)."""
        current = _load_key(settings.SECRETS_ENCRYPTION_KEY)
        if current is None:
            raise SecretManagerNotConfigured(
                "SECRETS_ENCRYPTION_KEY is unset — the aes256 secret backend "
                "is not configured")
        keys = {_key_id(current): current}
        previous = _load_key(settings.SECRETS_ENCRYPTION_KEY_PREVIOUS)
        if previous is not None:
            keys.setdefault(_key_id(previous), previous)
        return keys

    def _encrypt(self, secrets: Dict[str, Any]) -> tuple[str, str]:
        keys = self._keys()
        key_id, key = next(iter(keys.items()))
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(key).encrypt(
            nonce, json.dumps(secrets).encode("utf-8"), None)
        return base64.b64encode(nonce + ciphertext).decode("ascii"), key_id

    def _decrypt(self, row: ConnectionSecret) -> Dict[str, Any]:
        blob = base64.b64decode(row.ciphertext)
        nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        keys = self._keys()
        tried = [keys[row.key_id]] if row.key_id in keys else []
        tried += [k for kid, k in keys.items() if kid != row.key_id]
        for key in tried:
            try:
                plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
                return json.loads(plaintext.decode("utf-8"))
            except Exception:
                continue
        raise SecretManagerError(
            f"could not decrypt connection secret row {row.id} — the encrypting "
            f"key (key_id={row.key_id}) is no longer available")

    # ── SecretManager interface ───────────────────────────────────────────

    def store(self, connection_id: int, secrets: Dict[str, Any],
              db: Optional[Session] = None) -> str:
        ciphertext, key_id = self._encrypt(secrets)
        with _session(db) as (s, own):
            row = (
                s.query(ConnectionSecret)
                .filter(ConnectionSecret.connection_id == connection_id)
                .first()
            )
            if row:  # upsert — the unique constraint makes this race-safe
                row.ciphertext = ciphertext
                row.key_id = key_id
            else:
                row = ConnectionSecret(connection_id=connection_id,
                                       ciphertext=ciphertext, key_id=key_id)
                s.add(row)
            s.flush()
            ref = f"{_REF_PREFIX}{row.id}"
            if own:
                s.commit()
        logger.info("[secrets] stage=store connection_id=%d key_id=%s", connection_id, key_id)
        return ref

    def retrieve(self, secrets_ref: str, db: Optional[Session] = None) -> Dict[str, Any]:
        row_id = self._row_id(secrets_ref)
        with _session(db) as (s, _own):
            row = s.query(ConnectionSecret).filter(ConnectionSecret.id == row_id).first()
            if row is None:
                raise SecretManagerError(f"no vault row for ref {secrets_ref}")
            return self._decrypt(row)

    def rotate(self, secrets_ref: str, new_secrets: Dict[str, Any],
               db: Optional[Session] = None) -> str:
        row_id = self._row_id(secrets_ref)
        ciphertext, key_id = self._encrypt(new_secrets)
        with _session(db) as (s, own):
            row = s.query(ConnectionSecret).filter(ConnectionSecret.id == row_id).first()
            if row is None:
                raise SecretManagerError(f"no vault row for ref {secrets_ref}")
            row.ciphertext = ciphertext
            row.key_id = key_id
            row.rotated_at = datetime.now(timezone.utc)
            s.flush()
            if own:
                s.commit()
        logger.info("[secrets] stage=rotate ref=%s key_id=%s", secrets_ref, key_id)
        return secrets_ref  # row id is stable across rotation

    def delete(self, secrets_ref: str, db: Optional[Session] = None) -> None:
        row_id = self._row_id(secrets_ref)
        with _session(db) as (s, own):
            row = s.query(ConnectionSecret).filter(ConnectionSecret.id == row_id).first()
            if row is not None:
                s.delete(row)
                s.flush()
                if own:
                    s.commit()
        logger.info("[secrets] stage=delete ref=%s", secrets_ref)

    @staticmethod
    def _row_id(secrets_ref: str) -> int:
        if not secrets_ref.startswith(_REF_PREFIX):
            raise SecretManagerError(f"not an aes256 ref: {secrets_ref}")
        return int(secrets_ref[len(_REF_PREFIX):])
