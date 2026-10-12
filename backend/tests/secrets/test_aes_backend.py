"""AES-256-GCM backend tests (keeperdb_integration_tasks #3):
roundtrip, rotation, key rotation, ciphertext-at-rest, missing-key config."""
from __future__ import annotations

import base64

import pytest

from app.models.connection_secret import ConnectionSecret
from app.services.aes_gcm_secret_manager import AesGcmSecretManager
from app.services.secret_manager import (
    SecretManagerError,
    SecretManagerNotConfigured,
    get_secret_manager,
    secret_manager_enabled,
)
from tests.secrets.conftest import SECRET_VALUE, TEST_KEY, TEST_KEY_2


def test_factory_returns_aes_backend_by_default():
    assert isinstance(get_secret_manager(), AesGcmSecretManager)
    assert secret_manager_enabled() is True


def test_store_retrieve_roundtrip(db):
    mgr = AesGcmSecretManager()
    ref = mgr.store(1, {"password": SECRET_VALUE}, db=db)
    db.commit()
    assert ref.startswith("aes256://")
    assert mgr.retrieve(ref, db=db) == {"password": SECRET_VALUE}


def test_ciphertext_at_rest_is_not_plaintext(db):
    mgr = AesGcmSecretManager()
    mgr.store(1, {"password": SECRET_VALUE}, db=db)
    db.commit()
    row = db.query(ConnectionSecret).one()
    assert SECRET_VALUE not in row.ciphertext
    assert SECRET_VALUE.encode() not in base64.b64decode(row.ciphertext)
    assert row.key_id  # key identifier, not key material


def test_rotate_updates_value_and_keeps_ref(db):
    mgr = AesGcmSecretManager()
    ref = mgr.store(1, {"password": "old"}, db=db)
    db.commit()
    new_ref = mgr.rotate(ref, {"password": SECRET_VALUE}, db=db)
    db.commit()
    assert new_ref == ref
    assert mgr.retrieve(ref, db=db)["password"] == SECRET_VALUE
    assert db.query(ConnectionSecret).one().rotated_at is not None


def test_key_rotation_previous_key_still_reads(db, monkeypatch):
    from app.core.config import settings
    mgr = AesGcmSecretManager()
    ref = mgr.store(1, {"password": SECRET_VALUE}, db=db)
    db.commit()

    # Key changes; previous key configured for the transition window.
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", TEST_KEY_2)
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY_PREVIOUS", TEST_KEY)
    assert mgr.retrieve(ref, db=db)["password"] == SECRET_VALUE

    # rotate() re-encrypts under the NEW key.
    mgr.rotate(ref, {"password": SECRET_VALUE}, db=db)
    db.commit()
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY_PREVIOUS", None)
    assert mgr.retrieve(ref, db=db)["password"] == SECRET_VALUE


def test_lost_key_is_a_clear_error_without_the_value(db, monkeypatch):
    from app.core.config import settings
    mgr = AesGcmSecretManager()
    ref = mgr.store(1, {"password": SECRET_VALUE}, db=db)
    db.commit()
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", TEST_KEY_2)  # old key gone
    with pytest.raises(SecretManagerError) as exc:
        mgr.retrieve(ref, db=db)
    assert SECRET_VALUE not in str(exc.value)


def test_unset_key_means_not_configured(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", None)
    assert secret_manager_enabled() is False
    with pytest.raises(SecretManagerNotConfigured):
        AesGcmSecretManager().store(1, {"password": "x"})


def test_bad_key_rejected(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY",
                        base64.b64encode(b"short").decode())
    with pytest.raises(SecretManagerNotConfigured, match="32 bytes"):
        AesGcmSecretManager().store(1, {"password": "x"})


def test_delete_removes_row(db):
    mgr = AesGcmSecretManager()
    ref = mgr.store(1, {"password": SECRET_VALUE}, db=db)
    db.commit()
    mgr.delete(ref, db=db)
    db.commit()
    assert db.query(ConnectionSecret).count() == 0
    with pytest.raises(SecretManagerError):
        mgr.retrieve(ref, db=db)


def test_store_upserts_one_row_per_connection(db):
    mgr = AesGcmSecretManager()
    ref1 = mgr.store(7, {"password": "a"}, db=db)
    db.commit()
    ref2 = mgr.store(7, {"password": "b"}, db=db)
    db.commit()
    assert ref1 == ref2
    assert db.query(ConnectionSecret).count() == 1
    assert mgr.retrieve(ref1, db=db)["password"] == "b"
