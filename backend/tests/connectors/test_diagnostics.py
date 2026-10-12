"""Driver-level diagnostics + timeout enforcement (task #4, FR4)."""
import time

from app.connectors.base import TestConnectionResult, classify_connection_error
from app.connectors.sqlite import SQLiteConnector
from app.core.config import settings
from app.models.connection import DBConnection
from app.services import schema_service
from app.services.schema_service import SchemaService


# ── classification heuristics ────────────────────────────────────

def test_classify_timeout():
    r = classify_connection_error("connection timed out")
    assert r.error_code == "CONNECTION_TIMEOUT"
    assert r.reachable is False


def test_classify_refused():
    r = classify_connection_error("could not connect to server: Connection refused")
    assert r.error_code == "CONNECTION_REFUSED"
    assert r.reachable is False


def test_classify_auth():
    r = classify_connection_error('FATAL: password authentication failed for user "u"')
    assert r.error_code == "AUTH_FAILED"
    assert r.reachable is True
    assert r.authenticated is False


def test_classify_database_unavailable():
    r = classify_connection_error('FATAL: database "nope" does not exist')
    assert r.error_code == "DATABASE_UNAVAILABLE"
    assert r.authenticated is True
    assert r.database_accessible is False


def test_classify_unknown():
    r = classify_connection_error("some exotic driver failure")
    assert r.error_code == "UNKNOWN_ERROR"
    assert r.success is False
    assert r.error_message == "some exotic driver failure"


# ── sqlite driver ────────────────────────────────────────────────

def test_sqlite_success_has_version_and_latency(sqlite_file):
    result = SQLiteConnector(sqlite_file).test_connection()
    assert result.success is True
    assert result.version.startswith("SQLite")
    assert result.latency_ms is not None and result.latency_ms >= 0


def test_sqlite_missing_file_is_refused_not_created(tmp_path):
    path = str(tmp_path / "never_created.db")
    result = SQLiteConnector(path).test_connection()
    assert result.success is False
    assert result.error_code == "CONNECTION_REFUSED"
    # the test must not have created the file as a side effect
    import os
    assert not os.path.exists(path)


# ── service-level timeout + config errors ────────────────────────

def test_invalid_config_reported_not_raised(db):
    conn = DBConnection(name="broken", type="postgres", config={"host": "h"})
    db.add(conn)
    db.commit()
    result = SchemaService.test_connection(conn)
    assert result.success is False
    assert result.error_code == "INVALID_CONFIG"


def test_timeout_enforced(db, sqlite_conn, monkeypatch):
    class StallingConnector:
        def test_connection(self):
            time.sleep(3)
            return TestConnectionResult(success=True)

        def close(self):
            pass

    monkeypatch.setattr(schema_service, "get_connector",
                        lambda conn: StallingConnector())
    monkeypatch.setattr(settings, "CONNECTOR_TEST_TIMEOUT_SECONDS", 1)

    start = time.monotonic()
    result = SchemaService.test_connection(sqlite_conn)
    elapsed = time.monotonic() - start

    assert result.success is False
    assert result.error_code == "CONNECTION_TIMEOUT"
    assert "timed out after 1 seconds" in result.error_message
    assert elapsed < 2.5  # returned at the timeout, not after the 3s stall


def test_service_never_raises_on_driver_explosion(db, sqlite_conn, monkeypatch):
    class ExplodingConnector:
        def test_connection(self):
            raise RuntimeError("kaboom")

        def close(self):
            pass

    monkeypatch.setattr(schema_service, "get_connector",
                        lambda conn: ExplodingConnector())
    # a driver bug surfaces as a failed future -> the exception propagates
    # from future.result(); SchemaService must still return a result
    result = SchemaService.test_connection(sqlite_conn)
    assert result.success is False
