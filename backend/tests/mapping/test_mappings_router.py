"""Integration tests for /api/v1/mappings router."""
import pytest
from fastapi.testclient import TestClient

from app.api.deps import require_role as _require_role_factory
from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app
from app.services import schema_service


def _fake_schema(_conn):
    return {"dummy": [{"name": "c1", "type": "TEXT"}]}


def _patched_schema():
    return staticmethod(_fake_schema)


@pytest.fixture()
def client_admin(db, admin, monkeypatch):
    """TestClient wired to admin user + in-memory DB + stubbed schema fetch."""
    def _override_current_user():
        return admin
    app.dependency_overrides[get_current_user] = _override_current_user
    # Also override the closure that require_role captures at definition time.
    # require_role uses Depends(get_current_user), so the above is sufficient.

    def _get_db_override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[db_module.get_db] = _get_db_override
    monkeypatch.setattr(
        schema_service.SchemaService, "get_full_schema",
        staticmethod(_fake_schema),
    )
    # NOTE: We intentionally do NOT use `with TestClient(app)` because the
    # production lifespan in app.main.py seeds /shared/data on the host
    # filesystem. Tests run against an in-memory SQLite created by conftest,
    # so the lifespan is not needed.
    c = TestClient(app)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_analyst(db, analyst, monkeypatch):
    def _override():
        return analyst
    app.dependency_overrides[get_current_user] = _override

    def _get_db_override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[db_module.get_db] = _get_db_override
    monkeypatch.setattr(
        schema_service.SchemaService, "get_full_schema",
        staticmethod(_fake_schema),
    )
    c = TestClient(app)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_viewer(db, viewer, monkeypatch):
    def _override():
        return viewer
    app.dependency_overrides[get_current_user] = _override

    def _get_db_override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[db_module.get_db] = _get_db_override
    monkeypatch.setattr(
        schema_service.SchemaService, "get_full_schema",
        staticmethod(_fake_schema),
    )
    c = TestClient(app)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


def test_create_mapping_201(client_admin, seeded_connections):
    src, tgt = seeded_connections
    res = client_admin.post(
        "/api/v1/mappings/",
        json={"name": "Test", "source_id": src.id, "target_id": tgt.id},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "draft"
    assert "edges" in body


def test_list_mappings_returns_list(client_admin):
    # Review §11.8: paginated shape -- {items, total, limit, offset, has_more}
    # instead of a bare array, so the sidebar can page through large tenants.
    res = client_admin.get("/api/v1/mappings/")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    for key in ("total", "limit", "offset", "has_more"):
        assert key in body


def test_list_mappings_pagination_params(client_admin, seeded_connections):
    src, tgt = seeded_connections
    for i in range(3):
        res = client_admin.post("/api/v1/mappings/", json={
            "name": f"Page mapping {i}", "source_id": src.id, "target_id": tgt.id,
        })
        assert res.status_code == 201

    res = client_admin.get("/api/v1/mappings/", params={"limit": 2, "offset": 0})
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert body["total"] >= 3
    assert body["has_more"] is True

    res = client_admin.get("/api/v1/mappings/", params={"limit": 2, "offset": 2})
    assert res.status_code == 200
    body2 = res.json()
    assert len(body2["items"]) >= 1
    # No overlap between the two pages.
    ids_page1 = {m["id"] for m in body["items"]}
    ids_page2 = {m["id"] for m in body2["items"]}
    assert ids_page1.isdisjoint(ids_page2)


def test_viewer_can_list_but_not_create(client_viewer, seeded_connections):
    res = client_viewer.get("/api/v1/mappings/")
    assert res.status_code == 200
    src, tgt = seeded_connections
    res = client_viewer.post(
        "/api/v1/mappings/",
        json={"name": "X", "source_id": src.id, "target_id": tgt.id},
    )
    assert res.status_code == 403


def test_analyst_cannot_publish(client_analyst, seeded_connections):
    src, tgt = seeded_connections
    res = client_analyst.post(
        "/api/v1/mappings/",
        json={"name": "A", "source_id": src.id, "target_id": tgt.id},
    )
    assert res.status_code == 201
    mid = res.json()["id"]
    res = client_analyst.post(
        f"/api/v1/mappings/{mid}/edges",
        json={
            "target": {"table": "t1", "column": "c1", "type": "TEXT",
                        "nullable": False},
            "sources": [{"table": "s1", "column": "c1", "type": "TEXT",
                          "nullable": False}],
            "transformation": {"kind": "direct"},
            "origin": "manual",
        },
    )
    assert res.status_code == 201
    res = client_analyst.post(f"/api/v1/mappings/{mid}/publish")
    assert res.status_code == 403


def test_admin_full_flow_create_edge_validate_publish_export(
    client_admin, seeded_connections,
):
    src, tgt = seeded_connections
    res = client_admin.post(
        "/api/v1/mappings/",
        json={"name": "Flow", "source_id": src.id, "target_id": tgt.id},
    )
    assert res.status_code == 201
    mid = res.json()["id"]

    res = client_admin.post(
        f"/api/v1/mappings/{mid}/edges",
        json={
            "target": {"table": "t1", "column": "c1", "type": "TEXT",
                        "nullable": False},
            "sources": [{"table": "s1", "column": "c1", "type": "TEXT",
                          "nullable": False}],
            "transformation": {"kind": "direct"},
            "origin": "manual",
        },
    )
    assert res.status_code == 201, res.text

    res = client_admin.post(f"/api/v1/mappings/{mid}/validate")
    assert res.status_code == 200
    assert res.json()["blocking_count"] == 0

    res = client_admin.post(f"/api/v1/mappings/{mid}/publish")
    assert res.status_code == 200, res.text
    assert res.json()["version_number"] == 1

    res = client_admin.get(f"/api/v1/mappings/{mid}/export")
    assert res.status_code == 200, res.text
    artifact = res.json()
    assert artifact["version"] == 1
    assert len(artifact["field_mappings"]) == 1


def test_publish_blocked_by_incompatible_types(
    client_admin, seeded_connections, monkeypatch,
):
    monkeypatch.setattr(
        schema_service.SchemaService, "get_full_schema",
        staticmethod(lambda conn: {"t1": [{"name": "c1", "type": "INTEGER"}]}),
    )
    src, tgt = seeded_connections
    res = client_admin.post(
        "/api/v1/mappings/",
        json={"name": "Block", "source_id": src.id, "target_id": tgt.id},
    )
    mid = res.json()["id"]
    res = client_admin.post(
        f"/api/v1/mappings/{mid}/edges",
        json={
            "target": {"table": "t1", "column": "c1", "type": "INTEGER",
                        "nullable": False},
            "sources": [{"table": "s1", "column": "c1", "type": "TEXT",
                          "nullable": False}],
            "transformation": {"kind": "direct"},
            "origin": "manual",
        },
    )
    assert res.status_code == 201
    res = client_admin.post(f"/api/v1/mappings/{mid}/publish")
    assert res.status_code == 422
    assert res.json()["detail"]["kind"] == "validation_blocking"


def test_get_mapping_returns_mapping(client_admin, seeded_connections):
    src, tgt = seeded_connections
    res = client_admin.post(
        "/api/v1/mappings/",
        json={"name": "Get", "source_id": src.id, "target_id": tgt.id},
    )
    mid = res.json()["id"]
    res = client_admin.get(f"/api/v1/mappings/{mid}")
    assert res.status_code == 200
    assert res.json()["id"] == mid


def test_get_unknown_mapping_returns_404(client_admin):
    res = client_admin.get("/api/v1/mappings/99999")
    assert res.status_code == 404
