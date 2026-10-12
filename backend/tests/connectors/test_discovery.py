"""Schema discovery handoff (task #6, FR8)."""
import sqlite3

from app.models.audit import AuditLog
from app.models.schema_catalog import CatalogTable
from app.models.schema_snapshot import SchemaSnapshot


def test_discover_creates_snapshot_and_catalog(client_admin, db, sqlite_conn):
    r = client_admin.post(f"/api/v1/connectors/{sqlite_conn.id}/discover")
    assert r.status_code == 200
    body = r.json()
    assert body["tables"] == 1
    assert body["tables_discovered"] == ["users"]

    snap = db.query(SchemaSnapshot).filter(
        SchemaSnapshot.id == body["snapshot_id"]).one()
    assert snap.connection_id == sqlite_conn.id
    assert "users" in snap.schema_json

    # Schema Intel handoff persisted catalog rows
    assert body["catalog_scan"]["tables_scanned"] == 1
    tables = db.query(CatalogTable).filter(
        CatalogTable.connection_id == sqlite_conn.id).all()
    assert [t.table_name for t in tables] == ["users"]

    audit = db.query(AuditLog).filter(
        AuditLog.event_type == "discovery_completed").one()
    assert audit.payload["catalog_handoff"] is True


def test_discover_empty_schema_succeeds(client_admin, db, tmp_path):
    path = str(tmp_path / "empty.db")
    sqlite3.connect(path).close()  # valid but table-less database
    created = client_admin.post("/api/v1/connectors/", json={
        "name": "empty-db", "type": "sqlite", "config": {"path": path}})
    cid = created.json()["id"]

    r = client_admin.post(f"/api/v1/connectors/{cid}/discover")
    assert r.status_code == 200
    assert r.json()["tables"] == 0
    assert r.json()["tables_discovered"] == []


def test_discover_deleted_connection_404(client_admin, sqlite_conn):
    client_admin.delete(f"/api/v1/connectors/{sqlite_conn.id}")
    r = client_admin.post(f"/api/v1/connectors/{sqlite_conn.id}/discover")
    assert r.status_code == 404


def test_discover_degrades_when_catalog_scan_fails(client_admin, db, sqlite_conn,
                                                   monkeypatch):
    from app.services.schema_catalog_service import SchemaCatalogService

    def _boom(*a, **k):
        raise RuntimeError("catalog offline")

    monkeypatch.setattr(SchemaCatalogService, "scan_connection", _boom)
    r = client_admin.post(f"/api/v1/connectors/{sqlite_conn.id}/discover")
    assert r.status_code == 200
    assert r.json()["catalog_scan"] is None
    # snapshot still persisted despite the failed handoff
    assert db.query(SchemaSnapshot).filter(
        SchemaSnapshot.connection_id == sqlite_conn.id).count() == 1


def test_discover_failure_is_audited(client_admin, db, monkeypatch):
    created = client_admin.post("/api/v1/connectors/", json={
        "name": "will-fail", "type": "sqlite", "config": {"path": "/tmp/f.db"}})
    cid = created.json()["id"]

    from app.services.schema_service import SchemaService

    def _boom(conn):
        raise RuntimeError("cannot read schema")

    monkeypatch.setattr(SchemaService, "get_full_schema", staticmethod(_boom))
    r = client_admin.post(f"/api/v1/connectors/{cid}/discover")
    assert r.status_code == 500
    audit = db.query(AuditLog).filter(
        AuditLog.event_type == "discovery_failed").one()
    assert audit.connection_id == cid
