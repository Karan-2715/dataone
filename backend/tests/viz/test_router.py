"""Router-level tests for /api/v1/viz (query + saved views)."""
import pytest
from fastapi.testclient import TestClient

from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app


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


def test_viewer_can_run_query(client_viewer, sales_connection):
    resp = client_viewer.post("/api/v1/viz/query", json={
        "connection_id": sales_connection.id, "table_name": "sales",
        "dimensions": ["region"],
        "measures": [{"field": "amount", "aggregation": "sum"}],
        "filters": [],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["columns"] == ["region", "sum_amount"]
    assert body["row_count"] == 3


def test_viewer_cannot_create_view(client_viewer, sales_connection):
    resp = client_viewer.post("/api/v1/viz/views", json={
        "name": "V", "connection_id": sales_connection.id, "table_name": "sales",
        "chart_type": "table", "dimensions": [], "measures": [], "filters": [],
    })
    assert resp.status_code == 403


def test_analyst_can_create_and_list_views(client_analyst, sales_connection):
    create_resp = client_analyst.post("/api/v1/viz/views", json={
        "name": "Analyst View", "connection_id": sales_connection.id, "table_name": "sales",
        "chart_type": "bar", "dimensions": ["region"],
        "measures": [{"field": "amount", "aggregation": "sum"}], "filters": [],
    })
    assert create_resp.status_code == 201
    view_id = create_resp.json()["id"]

    list_resp = client_analyst.get("/api/v1/viz/views")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    get_resp = client_analyst.get(f"/api/v1/viz/views/{view_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Analyst View"

    del_resp = client_analyst.delete(f"/api/v1/viz/views/{view_id}")
    assert del_resp.status_code == 204


def test_invalid_aggregation_returns_422(client_analyst, sales_connection):
    resp = client_analyst.post("/api/v1/viz/query", json={
        "connection_id": sales_connection.id, "table_name": "sales",
        "dimensions": ["region"],
        "measures": [{"field": "amount", "aggregation": "bogus"}],
        "filters": [],
    })
    assert resp.status_code == 422
