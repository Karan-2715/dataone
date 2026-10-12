"""ConnectionService wiring + backfill + rotation tests
(keeperdb_integration_tasks #4/#5/#6/#8)."""
from __future__ import annotations

import sqlite3

import pytest

from app.models.audit import AuditLog
from app.models.connection import DBConnection
from app.models.connection_secret import ConnectionSecret
from app.services.connection_secrets_service import resolve_connection_config
from app.services.connection_service import ConnectionService
from tests.secrets.conftest import SECRET_VALUE

PG_CONFIG = {"host": "db.example", "port": 5432, "dbname": "sales",
             "user": "svc", "password": SECRET_VALUE}


@pytest.fixture()
def pg_connection(db):
    return ConnectionService.create_connection(
        db, name="pg-src", conn_type="postgres",
        config=dict(PG_CONFIG), actor="admin@test.local",
        owner_email="admin@test.local")


# ── Create path (task #4) ────────────────────────────────────────────────

def test_create_stores_secret_in_vault_not_config(db, pg_connection):
    assert "password" not in pg_connection.config
    assert pg_connection.config["host"] == "db.example"
    assert pg_connection.secrets_ref and pg_connection.secrets_ref.startswith("aes256://")
    row = db.query(ConnectionSecret).one()
    assert SECRET_VALUE not in row.ciphertext


def test_get_endpoint_still_redacts(client_admin, db, pg_connection):
    resp = client_admin.get(f"/api/v1/connectors/{pg_connection.id}")
    body = resp.json()
    assert SECRET_VALUE not in resp.text
    assert "password" not in body["config"] or body["config"]["password"] != SECRET_VALUE


def test_sqlite_connection_never_touches_the_vault(db, tmp_path):
    path = str(tmp_path / "x.db")
    sqlite3.connect(path).close()
    conn = ConnectionService.create_connection(
        db, name="lite", conn_type="sqlite",
        config={"path": path}, actor="admin@test.local",
        owner_email="admin@test.local")
    assert conn.secrets_ref is None
    assert db.query(ConnectionSecret).count() == 0


def test_legacy_mode_keeps_secrets_in_config(db, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", None)
    conn = ConnectionService.create_connection(
        db, name="legacy-pg", conn_type="postgres",
        config=dict(PG_CONFIG), actor="admin@test.local",
        owner_email="admin@test.local")
    assert conn.secrets_ref is None
    assert conn.config["password"] == SECRET_VALUE  # legacy; redaction still guards responses


# ── Resolution for connectors (task #4) ──────────────────────────────────

def test_resolve_merges_vault_secrets_for_connector_use(db, pg_connection):
    resolved = resolve_connection_config(pg_connection)
    assert resolved["password"] == SECRET_VALUE
    assert resolved["host"] == "db.example"
    # And the model itself stays clean:
    assert "password" not in pg_connection.config


def test_get_connector_receives_resolved_credentials(db, pg_connection, monkeypatch):
    captured = {}

    class _FakePg:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("app.services.schema_service.PostgresConnector", _FakePg)
    from app.services.schema_service import get_connector
    get_connector(pg_connection)
    assert captured["password"] == SECRET_VALUE


# ── Hard/soft delete (task #4) ───────────────────────────────────────────

def test_hard_delete_removes_vault_entry(db, pg_connection):
    ConnectionService.soft_delete_connection(
        db, pg_connection.id, actor="admin@test.local",
        dependents={"mappings": [], "pipelines": [], "total": 0})
    ConnectionService.hard_delete_connection(db, pg_connection.id, actor="admin@test.local")
    assert db.query(ConnectionSecret).count() == 0


def test_soft_delete_retains_vault_entry_for_restore(db, pg_connection):
    ConnectionService.soft_delete_connection(
        db, pg_connection.id, actor="admin@test.local",
        dependents={"mappings": [], "pipelines": [], "total": 0})
    assert db.query(ConnectionSecret).count() == 1
    restored = ConnectionService.restore_connection(db, pg_connection.id, actor="admin@test.local")
    assert resolve_connection_config(restored)["password"] == SECRET_VALUE


# ── Backfill migration (task #5) ─────────────────────────────────────────

@pytest.fixture()
def legacy_connection(db, monkeypatch):
    """A pre-vaulting row: plaintext secret in config, no secrets_ref."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", None)
    conn = ConnectionService.create_connection(
        db, name="legacy-row", conn_type="postgres",
        config=dict(PG_CONFIG), actor="admin@test.local",
        owner_email="admin@test.local")
    from tests.secrets.conftest import TEST_KEY
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", TEST_KEY)
    return conn


def test_backfill_migrates_exactly_once(client_admin, db, legacy_connection):
    first = client_admin.post("/api/v1/connectors/migrate-secrets").json()
    assert first["migrated"] == [legacy_connection.id]
    db.refresh(legacy_connection)
    assert "password" not in legacy_connection.config
    assert legacy_connection.secrets_ref
    assert resolve_connection_config(legacy_connection)["password"] == SECRET_VALUE

    second = client_admin.post("/api/v1/connectors/migrate-secrets").json()
    assert second["migrated"] == []          # idempotent
    assert legacy_connection.id in second["skipped"]
    assert db.query(ConnectionSecret).count() == 1


def test_backfill_requires_admin(client_analyst):
    assert client_analyst.post("/api/v1/connectors/migrate-secrets").status_code == 403


def test_backfill_refuses_when_unconfigured(client_admin, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", None)
    resp = client_admin.post("/api/v1/connectors/migrate-secrets")
    assert resp.status_code == 409


# ── Rotation (task #6 — unblocks connector_tasks #8) ─────────────────────

def test_rotate_updates_vault_and_next_resolve_sees_it(client_admin, db, pg_connection):
    resp = client_admin.post(
        f"/api/v1/connectors/{pg_connection.id}/rotate-credentials",
        json={"secrets": {"password": "new-pw-after-rotation"}})
    assert resp.status_code == 200
    assert resp.json()["rotated_fields"] == ["password"]
    assert "new-pw-after-rotation" not in resp.text  # never echoed
    db.refresh(pg_connection)
    assert resolve_connection_config(pg_connection)["password"] == "new-pw-after-rotation"


def test_rotate_fails_closed_when_existing_secret_cannot_be_read(
        db, pg_connection, monkeypatch):
    """A partial rotation must never erase fields during a vault outage."""
    from app.services import connection_secrets_service as service
    from app.services.secret_manager import SecretManagerError

    class UnreadableManager:
        rotate_called = False

        def retrieve(self, _ref, db=None):
            raise SecretManagerError("vault unavailable")

        def rotate(self, _ref, _secrets, db=None):
            self.rotate_called = True
            return _ref

    manager = UnreadableManager()
    monkeypatch.setattr(service, "get_secret_manager", lambda: manager)

    with pytest.raises(SecretManagerError, match="vault unavailable"):
        service.rotate_credentials(
            db, pg_connection.id, {"password": "replacement"},
            actor="admin@test.local",
        )

    assert manager.rotate_called is False


def test_rotate_legacy_row_migrates_it(client_admin, db, legacy_connection):
    resp = client_admin.post(
        f"/api/v1/connectors/{legacy_connection.id}/rotate-credentials",
        json={"secrets": {"password": "rotated-in"}})
    assert resp.status_code == 200
    db.refresh(legacy_connection)
    assert "password" not in legacy_connection.config
    assert resolve_connection_config(legacy_connection)["password"] == "rotated-in"


def test_rotate_sqlite_rejected_clearly(client_admin, db, tmp_path):
    import sqlite3 as s3
    path = str(tmp_path / "y.db")
    s3.connect(path).close()
    conn = ConnectionService.create_connection(
        db, name="lite2", conn_type="sqlite", config={"path": path},
        actor="admin@test.local", owner_email="admin@test.local")
    resp = client_admin.post(
        f"/api/v1/connectors/{conn.id}/rotate-credentials",
        json={"secrets": {"password": "x"}})
    assert resp.status_code == 422
    assert "no credential fields" in resp.json()["detail"]


def test_rotate_unknown_field_rejected(client_admin, pg_connection):
    resp = client_admin.post(
        f"/api/v1/connectors/{pg_connection.id}/rotate-credentials",
        json={"secrets": {"host": "evil"}})
    assert resp.status_code == 422


def test_rotate_requires_admin(client_analyst, pg_connection):
    resp = client_analyst.post(
        f"/api/v1/connectors/{pg_connection.id}/rotate-credentials",
        json={"secrets": {"password": "x"}})
    assert resp.status_code == 403


def test_malformed_rotate_body_does_not_echo(client_admin, pg_connection):
    resp = client_admin.post(
        f"/api/v1/connectors/{pg_connection.id}/rotate-credentials",
        json={"secrets": {"password": 12345}})  # wrong type
    assert resp.status_code == 422
    assert "12345" not in resp.text  # invalid input is not echoed back


# ── Audit events (task #8) ───────────────────────────────────────────────

def test_store_rotate_delete_audited_without_values(client_admin, db, pg_connection):
    client_admin.post(
        f"/api/v1/connectors/{pg_connection.id}/rotate-credentials",
        json={"secrets": {"password": "rotated-secret-xyz"}})
    events = {
        r.event_type: r for r in
        db.query(AuditLog).filter(AuditLog.module == "secrets").all()
    }
    assert "secrets.secret_store" in events
    assert "secrets.secret_rotate" in events
    for row in events.values():
        serialized = str(row.event_metadata) + str(row.summary)
        assert SECRET_VALUE not in serialized
        assert "rotated-secret-xyz" not in serialized
    assert events["secrets.secret_rotate"].event_metadata["fields"] == ["password"]


def test_retrieve_audit_batched_per_connection(db, pg_connection):
    for _ in range(5):
        resolve_connection_config(pg_connection)
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "secrets.secret_retrieve")
        .all()
    )
    assert len(rows) == 1  # one audit per TTL window, not one per call
    assert rows[0].event_metadata["batched_window_seconds"] == 60


# ── Backend selection fail-fast (v5 bugs2 #3) ────────────────────────────

def test_unknown_backend_fails_fast_not_silent_plaintext(monkeypatch):
    """A typo'd backend name must not silently fall back to legacy plaintext
    storage while the operator believes vaulting is on."""
    from app.core.config import settings
    from app.services.secret_manager import (
        SecretManagerNotConfigured, get_secret_manager, secret_manager_enabled)
    monkeypatch.setattr(settings, "SECRET_MANAGER_BACKEND", "ksm")  # typo for 'keeper'
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", None)
    with pytest.raises(SecretManagerNotConfigured):
        secret_manager_enabled()
    with pytest.raises(SecretManagerNotConfigured):
        get_secret_manager()


def test_config_rejects_unknown_backend_at_construction():
    """Boot-time guard: an invalid SECRET_MANAGER_BACKEND env value is rejected
    when Settings is constructed, not tolerated until first vault use."""
    from pydantic import ValidationError
    from app.core.config import Settings
    with pytest.raises(ValidationError):
        Settings(SECRET_MANAGER_BACKEND="bogus")
