"""No-plaintext-leak + outage verification (keeperdb_integration_tasks #11 —
the actual proof of AC5/AC6, not optional polish).

Asserts the secret value never appears in: HTTP response bodies (including
422 echoes), captured log output at any level, or audit payloads — across
every vault operation. Plus: a vault outage leaves metadata reads working
and fails only credential-dependent operations, clearly.
"""
from __future__ import annotations

import logging

import pytest

from app.models.audit import AuditLog
from app.services.connection_service import ConnectionService
from tests.secrets.conftest import SECRET_VALUE

PG_CONFIG = {"host": "db.example", "port": 5432, "dbname": "sales",
             "user": "svc", "password": SECRET_VALUE}


def _assert_no_secret_in_logs(caplog):
    for record in caplog.records:
        assert SECRET_VALUE not in record.getMessage(), (
            f"secret leaked into log: {record.name}:{record.levelname}")


def test_full_lifecycle_leaks_nothing(client_admin, db, caplog):
    """create → GET → list → test 422 → rotate → hard delete, grepping every
    surface for the secret value."""
    caplog.set_level(logging.DEBUG)

    # Create
    resp = client_admin.post("/api/v1/connectors/", json={
        "name": "leaktest", "type": "postgres", "config": dict(PG_CONFIG)})
    assert resp.status_code == 201
    assert SECRET_VALUE not in resp.text
    conn_id = resp.json()["id"]

    # Read endpoints
    assert SECRET_VALUE not in client_admin.get(f"/api/v1/connectors/{conn_id}").text
    assert SECRET_VALUE not in client_admin.get("/api/v1/connectors/").text

    # 422 validation echo (connector_tasks #2's called-out risk): an invalid
    # create carrying the secret must not bounce it back.
    bad = client_admin.post("/api/v1/connectors/", json={
        "name": "x" * 200,  # invalid name triggers 422
        "type": "postgres", "config": dict(PG_CONFIG)})
    assert bad.status_code == 422
    assert SECRET_VALUE not in bad.text

    # Rotate
    rot = client_admin.post(f"/api/v1/connectors/{conn_id}/rotate-credentials",
                            json={"secrets": {"password": SECRET_VALUE}})
    assert rot.status_code == 200
    assert SECRET_VALUE not in rot.text

    # Hard delete (soft first)
    client_admin.delete(f"/api/v1/connectors/{conn_id}?confirm=true")
    client_admin.delete(f"/api/v1/connectors/{conn_id}/hard")

    # Logs — every module, every level.
    _assert_no_secret_in_logs(caplog)

    # Audit payloads — serialized, to catch str()-ed dicts too.
    for row in db.query(AuditLog).all():
        assert SECRET_VALUE not in str(row.event_metadata or "")
        assert SECRET_VALUE not in str(row.summary or "")


def test_migration_leaks_nothing(client_admin, db, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    from app.core.config import settings
    from tests.secrets.conftest import TEST_KEY

    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", None)
    ConnectionService.create_connection(
        db, name="legacy-leak", conn_type="postgres",
        config=dict(PG_CONFIG), actor="admin@test.local",
        owner_email="admin@test.local")
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", TEST_KEY)

    resp = client_admin.post("/api/v1/connectors/migrate-secrets")
    assert resp.status_code == 200
    assert SECRET_VALUE not in resp.text
    _assert_no_secret_in_logs(caplog)
    for row in db.query(AuditLog).all():
        assert SECRET_VALUE not in str(row.event_metadata or "")


# ── Outage behavior (AC5): vault down ≠ platform down ────────────────────

def test_vault_outage_leaves_metadata_reads_working(client_admin, db, monkeypatch):
    resp = client_admin.post("/api/v1/connectors/", json={
        "name": "outage-conn", "type": "postgres", "config": dict(PG_CONFIG)})
    conn_id = resp.json()["id"]

    # Simulate the vault being unavailable (e.g. Keeper unreachable / key
    # service down) for every retrieve.
    from app.services import connection_secrets_service as svc
    from app.services.secret_manager import SecretManagerError

    class _DeadManager:
        def retrieve(self, ref, db=None):
            raise SecretManagerError("vault unreachable")

    monkeypatch.setattr(svc, "get_secret_manager", lambda: _DeadManager())

    # Metadata reads: completely unaffected (never touch the vault).
    assert client_admin.get(f"/api/v1/connectors/{conn_id}").status_code == 200
    assert client_admin.get("/api/v1/connectors/").status_code == 200
    assert client_admin.get("/api/v1/connectors/health-summary").status_code == 200

    # Credential-dependent operation: fails with a clear error, not a hang.
    conn = ConnectionService.get_connection(db, conn_id)
    from app.services.schema_service import get_connector
    with pytest.raises(SecretManagerError, match="vault unreachable"):
        get_connector(conn)


def test_keeper_circuit_open_fails_fast_for_credentials_only(client_admin, db, monkeypatch):
    from app.core.circuit_breaker import CircuitBreakerOpen
    from app.services import connection_secrets_service as svc

    resp = client_admin.post("/api/v1/connectors/", json={
        "name": "breaker-conn", "type": "postgres", "config": dict(PG_CONFIG)})
    conn_id = resp.json()["id"]

    class _OpenCircuitManager:
        def retrieve(self, ref, db=None):
            raise CircuitBreakerOpen("Circuit 'keeper' is OPEN — failing fast")

    monkeypatch.setattr(svc, "get_secret_manager", lambda: _OpenCircuitManager())

    assert client_admin.get(f"/api/v1/connectors/{conn_id}").status_code == 200
    conn = ConnectionService.get_connection(db, conn_id)
    from app.services.schema_service import get_connector
    with pytest.raises(CircuitBreakerOpen):
        get_connector(conn)
