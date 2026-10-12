"""Role-gating tests for the new Schema Intel catalog endpoints (Tasks #2, #7):
POST /catalog/{id}/profile and PUT /catalog/columns/{id}/classification.

Mirrors the TestClient pattern in tests/pipelines/test_router_role_gating.py.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app
from app.services.schema_catalog_service import SchemaCatalogService


@pytest.fixture(autouse=True)
def stub_celery_dispatch(monkeypatch):
    from app.tasks import schema_intel_tasks as sit_module
    monkeypatch.setattr(
        sit_module.profile_connection_task, "delay",
        lambda *a, **kw: SimpleNamespace(id="fake-task-id"),
    )


def _client_for(db, user):
    def _override_user():
        return user

    def _override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[db_module.get_db] = _override_db
    return TestClient(app)


@pytest.fixture()
def client_viewer(db, viewer):
    c = _client_for(db, viewer)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_analyst(db, analyst):
    c = _client_for(db, analyst)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


def test_viewer_cannot_trigger_profiling(client_viewer, physical_sqlite_connection, db):
    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    resp = client_viewer.post(f"/api/v1/catalog/{physical_sqlite_connection.id}/profile")
    assert resp.status_code == 403


def test_analyst_can_trigger_profiling(client_analyst, physical_sqlite_connection, db):
    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    resp = client_analyst.post(f"/api/v1/catalog/{physical_sqlite_connection.id}/profile")
    assert resp.status_code == 202
    assert resp.json()["task_id"] == "fake-task-id"


def test_profile_without_scan_returns_400(client_analyst, db):
    from app.models.connection import DBConnection
    conn = DBConnection(name="Unscanned2", type="sqlite", config={"path": "/tmp/unscanned2.db"})
    db.add(conn)
    db.commit()
    db.refresh(conn)

    resp = client_analyst.post(f"/api/v1/catalog/{conn.id}/profile")
    assert resp.status_code == 400


def test_viewer_cannot_override_classification(client_viewer, physical_sqlite_connection, db):
    from app.models.schema_catalog import CatalogColumn
    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    col = db.query(CatalogColumn).filter(CatalogColumn.column_name == "contact").first()

    resp = client_viewer.put(
        f"/api/v1/catalog/columns/{col.id}/classification",
        json={"label": "PII", "level": "High"},
    )
    assert resp.status_code == 403


def test_analyst_can_override_classification(client_analyst, physical_sqlite_connection, db):
    from app.models.schema_catalog import CatalogColumn
    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    col = db.query(CatalogColumn).filter(CatalogColumn.column_name == "contact").first()

    resp = client_analyst.put(
        f"/api/v1/catalog/columns/{col.id}/classification",
        json={"label": "PII", "level": "High"},
    )
    assert resp.status_code == 200
    assert resp.json()["method"] == "manual_override"


def test_search_query_param_filters_results(client_analyst, physical_sqlite_connection, db):
    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    resp = client_analyst.get(f"/api/v1/catalog/{physical_sqlite_connection.id}/tables?q=nonexistent_xyz")
    assert resp.status_code == 200
    assert resp.json()["tables"] == []
