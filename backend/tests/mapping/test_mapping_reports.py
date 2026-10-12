"""Tests for Mapping Documentation & Migration Reports (Enterprise v2, E15)."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app
from app.models.connection import DBConnection
from app.models.mapping import FieldMapping, Mapping


@pytest.fixture()
def client_admin(db, admin):
    def _override():
        return admin
    app.dependency_overrides[get_current_user] = _override

    def _get_db_override():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[db_module.get_db] = _get_db_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def physical_connections(db, tmp_path):
    src_path = str(tmp_path / "report_src.db")
    tgt_path = str(tmp_path / "report_tgt.db")
    src_conn = sqlite3.connect(src_path)
    src_conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    src_conn.commit()
    src_conn.close()
    tgt_conn = sqlite3.connect(tgt_path)
    tgt_conn.execute("CREATE TABLE customers (cust_id INTEGER PRIMARY KEY, full_name TEXT)")
    tgt_conn.commit()
    tgt_conn.close()

    src = DBConnection(name="ReportSrc", type="sqlite", config={"path": src_path})
    tgt = DBConnection(name="ReportTgt", type="sqlite", config={"path": tgt_path})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)
    return src, tgt


@pytest.fixture()
def mapping_with_edge(db, physical_connections):
    src, tgt = physical_connections
    m = Mapping(name="Report Test Mapping", source_id=src.id, target_id=tgt.id, status="draft", created_by="test")
    db.add(m)
    db.flush()
    db.add(FieldMapping(
        mapping_id=m.id, target_table="customers", target_column="full_name",
        sources=[{"table": "users", "column": "name", "type": "TEXT"}],
        transformation={"kind": "trim"}, origin="manual", ai_confidence=0.92,
    ))
    db.commit()
    db.refresh(m)
    return m


class TestDocumentation:
    def test_anonymous_returns_401(self, mapping_with_edge):
        resp = TestClient(app).get(f"/api/v1/mappings/{mapping_with_edge.id}/documentation")
        assert resp.status_code == 401

    def test_documentation_includes_overview_and_field_mappings(self, client_admin, mapping_with_edge):
        resp = client_admin.get(f"/api/v1/mappings/{mapping_with_edge.id}/documentation")
        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == f"mapping-{mapping_with_edge.id}-documentation.md"
        content = body["content"]
        assert "Report Test Mapping" in content
        assert "ReportSrc" in content
        assert "ReportTgt" in content
        assert "users.name" in content
        assert "customers.full_name" in content
        assert "trim" in content
        assert "92%" in content

    def test_mapping_with_no_edges_is_honest_not_fabricated(self, client_admin, db, physical_connections):
        src, tgt = physical_connections
        m = Mapping(name="Empty Mapping", source_id=src.id, target_id=tgt.id, status="draft", created_by="test")
        db.add(m)
        db.commit()
        db.refresh(m)

        resp = client_admin.get(f"/api/v1/mappings/{m.id}/documentation")
        assert "no field mappings defined yet" in resp.json()["content"]

    def test_unknown_mapping_returns_404(self, client_admin):
        assert client_admin.get("/api/v1/mappings/999999/documentation").status_code == 404


class TestMigrationReport:
    def test_report_includes_validation_and_schema_comparison(self, client_admin, mapping_with_edge):
        resp = client_admin.get(f"/api/v1/mappings/{mapping_with_edge.id}/migration-report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == f"mapping-{mapping_with_edge.id}-migration-report.md"
        content = body["content"]
        assert "Migration Readiness Report" in content
        assert "## Validation" in content
        assert "## Schema Comparison" in content
        assert "matched table" in content
        assert "## Related Risk Findings" in content

    def test_unreachable_connection_degrades_schema_section_not_the_whole_report(
        self, client_admin, db, mapping_with_edge,
    ):
        target = db.query(DBConnection).filter(DBConnection.id == mapping_with_edge.target_id).first()
        target.config = {"path": "/nonexistent/does-not-exist.db"}
        db.commit()

        resp = client_admin.get(f"/api/v1/mappings/{mapping_with_edge.id}/migration-report")
        assert resp.status_code == 200
        content = resp.json()["content"]
        assert "Not available" in content
        assert "## Related Risk Findings" in content  # later sections still render

    def test_unknown_mapping_returns_404(self, client_admin):
        assert client_admin.get("/api/v1/mappings/999999/migration-report").status_code == 404
