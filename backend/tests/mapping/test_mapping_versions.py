"""Router tests for version history / diff / rollback (Enterprise v2, E13-5/6)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app
from app.services import schema_service
from app.services.mapping_service import MappingService


def _fake_schema(_conn):
    return {"dummy": [{"name": "c1", "type": "TEXT"}]}


def _make_client(db, user, monkeypatch):
    def _override():
        return user
    app.dependency_overrides[get_current_user] = _override

    def _get_db_override():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[db_module.get_db] = _get_db_override
    monkeypatch.setattr(schema_service.SchemaService, "get_full_schema", staticmethod(_fake_schema))
    c = TestClient(app)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_admin(db, admin, monkeypatch):
    yield from _make_client(db, admin, monkeypatch)


@pytest.fixture()
def client_analyst(db, analyst, monkeypatch):
    yield from _make_client(db, analyst, monkeypatch)


@pytest.fixture()
def client_viewer(db, viewer, monkeypatch):
    yield from _make_client(db, viewer, monkeypatch)


@pytest.fixture()
def published_mapping(db, admin, seeded_connections):
    """One mapping, published as v1 with a single c1 edge — built directly
    through the service layer so router tests focus on the new endpoints,
    not on re-proving add_edge/publish."""
    src, tgt = seeded_connections
    m = MappingService.create_mapping(
        db, source_id=src.id, target_id=tgt.id, name="Version Test", actor=admin.email,
    )
    MappingService.add_edge(
        db, m.id,
        target={"table": "t1", "column": "c1", "type": "TEXT", "nullable": False},
        sources=[{"table": "s1", "column": "c1", "type": "TEXT", "nullable": False}],
        transformation={"kind": "direct"},
        actor=admin.email,
    )
    MappingService.publish(db, m.id, actor=admin.email)
    db.refresh(m)
    return m


class TestListVersions:
    def test_lists_the_published_version_as_current(self, client_admin, published_mapping):
        resp = client_admin.get(f"/api/v1/mappings/{published_mapping.id}/versions")
        assert resp.status_code == 200, resp.json()
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["version_number"] == 1
        assert items[0]["is_current"] is True
        assert items[0]["edge_count"] == 1

    def test_unknown_mapping_404s(self, client_admin):
        resp = client_admin.get("/api/v1/mappings/999999/versions")
        assert resp.status_code == 404


class TestDiffVersions:
    def test_diff_between_the_only_version_and_itself_is_rejected(self, client_admin, published_mapping):
        v1_id = published_mapping.current_version_id
        resp = client_admin.get(
            f"/api/v1/mappings/{published_mapping.id}/versions/diff",
            params={"from_version_id": v1_id, "to_version_id": v1_id},
        )
        assert resp.status_code == 422

    def test_diff_after_a_revision_and_republish_shows_the_addition(self, client_admin, published_mapping):
        v1_id = published_mapping.current_version_id
        rb = client_admin.post(
            f"/api/v1/mappings/{published_mapping.id}/versions/{v1_id}/rollback",
        )
        assert rb.status_code == 200, rb.json()

        add = client_admin.post(
            f"/api/v1/mappings/{published_mapping.id}/edges",
            json={
                "target": {"table": "t1", "column": "c2", "type": "TEXT", "nullable": False},
                "sources": [{"table": "s1", "column": "c2", "type": "TEXT", "nullable": False}],
                "transformation": {"kind": "direct"},
            },
        )
        assert add.status_code == 201, add.json()

        pub = client_admin.post(f"/api/v1/mappings/{published_mapping.id}/publish")
        assert pub.status_code == 200, pub.json()
        v2_id = pub.json()["version_id"]

        diff = client_admin.get(
            f"/api/v1/mappings/{published_mapping.id}/versions/diff",
            params={"from_version_id": v1_id, "to_version_id": v2_id},
        )
        assert diff.status_code == 200, diff.json()
        body = diff.json()
        assert body["unchanged_count"] == 1
        assert [e["target"]["column"] for e in body["added"]] == ["c2"]
        assert body["removed"] == []


class TestRollback:
    def test_admin_can_reopen_a_published_mapping_for_editing(self, client_admin, published_mapping):
        v1_id = published_mapping.current_version_id
        resp = client_admin.post(
            f"/api/v1/mappings/{published_mapping.id}/versions/{v1_id}/rollback",
        )
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["status"] == "draft"
        assert body["review_stage"] == "draft"
        assert len(body["edges"]) == 1

        # And it is genuinely editable now, not just relabeled.
        add = client_admin.post(
            f"/api/v1/mappings/{published_mapping.id}/edges",
            json={
                "target": {"table": "t1", "column": "c2", "type": "TEXT", "nullable": False},
                "sources": [{"table": "s1", "column": "c2", "type": "TEXT", "nullable": False}],
                "transformation": {"kind": "direct"},
            },
        )
        assert add.status_code == 201, add.json()

    def test_analyst_can_also_reopen_for_editing(self, client_analyst, published_mapping):
        v1_id = published_mapping.current_version_id
        resp = client_analyst.post(
            f"/api/v1/mappings/{published_mapping.id}/versions/{v1_id}/rollback",
        )
        assert resp.status_code == 200, resp.json()

    def test_viewer_forbidden(self, client_viewer, published_mapping):
        v1_id = published_mapping.current_version_id
        resp = client_viewer.post(
            f"/api/v1/mappings/{published_mapping.id}/versions/{v1_id}/rollback",
        )
        assert resp.status_code == 403

    def test_rejects_rollback_on_a_mapping_that_is_already_a_draft(self, client_admin, db, admin, seeded_connections):
        src, tgt = seeded_connections
        m = MappingService.create_mapping(
            db, source_id=src.id, target_id=tgt.id, name="Still Draft", actor=admin.email,
        )
        resp = client_admin.post(f"/api/v1/mappings/{m.id}/versions/1/rollback")
        assert resp.status_code == 409

    def test_unknown_version_id_404s(self, client_admin, published_mapping):
        resp = client_admin.post(
            f"/api/v1/mappings/{published_mapping.id}/versions/999999/rollback",
        )
        assert resp.status_code == 404
