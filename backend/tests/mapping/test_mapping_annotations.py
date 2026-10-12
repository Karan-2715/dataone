"""Tests for async collaboration annotations/comments (Enterprise v2, E16-1/2/3)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app
from app.models.mapping import FieldMapping, Mapping
from app.models.user import User
from app.services.auth_service import AuthService


@pytest.fixture()
def make_client(db):
    def _make(user=None):
        def _get_db_override():
            try:
                yield db
            finally:
                pass
        app.dependency_overrides[db_module.get_db] = _get_db_override
        if user is not None:
            app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)
    try:
        yield _make
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client(make_client, admin):
    return make_client(admin)


@pytest.fixture()
def other_analyst(db):
    u = User(email="other-analyst@test.local", hashed_password=AuthService.hash_password("x"),
              role="analyst", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def mapping(db, seeded_connections):
    src, tgt = seeded_connections
    m = Mapping(name="Annotation Test Mapping", source_id=src.id, target_id=tgt.id,
                status="draft", created_by="test")
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


@pytest.fixture()
def edge(db, mapping):
    e = FieldMapping(
        mapping_id=mapping.id, target_table="t1", target_column="c1",
        sources=[{"table": "s1", "column": "c1", "type": "TEXT"}],
        transformation={"kind": "direct"},
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


class TestCreateAnnotation:
    def test_anonymous_returns_401(self, mapping):
        resp = TestClient(app).post(f"/api/v1/mappings/{mapping.id}/annotations", json={"body": "hi"})
        assert resp.status_code == 401

    def test_creates_a_mapping_level_comment(self, client, mapping):
        resp = client.post(f"/api/v1/mappings/{mapping.id}/annotations", json={"body": "Looks good to me"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["kind"] == "comment"
        assert body["edge_id"] is None
        assert body["parent_id"] is None
        assert body["author"] == "admin@test.local"
        assert body["body"] == "Looks good to me"

    def test_creates_an_edge_pinned_comment(self, client, mapping, edge):
        resp = client.post(
            f"/api/v1/mappings/{mapping.id}/annotations",
            json={"body": "Check this transform", "edge_id": edge.id},
        )
        assert resp.status_code == 201
        assert resp.json()["edge_id"] == edge.id

    def test_rejects_an_edge_from_a_different_mapping(self, client, mapping, edge, db, seeded_connections):
        src, tgt = seeded_connections
        other = Mapping(name="Other", source_id=src.id, target_id=tgt.id, status="draft", created_by="test")
        db.add(other)
        db.commit()
        db.refresh(other)
        resp = client.post(
            f"/api/v1/mappings/{other.id}/annotations",
            json={"body": "wrong mapping", "edge_id": edge.id},
        )
        assert resp.status_code == 422

    def test_creates_a_threaded_reply(self, client, mapping):
        parent = client.post(f"/api/v1/mappings/{mapping.id}/annotations", json={"body": "top level"}).json()
        reply = client.post(
            f"/api/v1/mappings/{mapping.id}/annotations",
            json={"body": "a reply", "parent_id": parent["id"]},
        )
        assert reply.status_code == 201
        assert reply.json()["parent_id"] == parent["id"]

    def test_rejects_empty_body(self, client, mapping):
        resp = client.post(f"/api/v1/mappings/{mapping.id}/annotations", json={"body": "   "})
        assert resp.status_code == 422

    def test_rejects_oversized_body(self, client, mapping):
        resp = client.post(f"/api/v1/mappings/{mapping.id}/annotations", json={"body": "x" * 5000})
        assert resp.status_code == 422

    def test_404_for_missing_mapping(self, client):
        resp = client.post("/api/v1/mappings/999999/annotations", json={"body": "hi"})
        assert resp.status_code == 404


class TestListAnnotations:
    def test_lists_oldest_first_and_excludes_deleted(self, client, mapping):
        first = client.post(f"/api/v1/mappings/{mapping.id}/annotations", json={"body": "first"}).json()
        client.post(f"/api/v1/mappings/{mapping.id}/annotations", json={"body": "second"})
        client.delete(f"/api/v1/mappings/{mapping.id}/annotations/{first['id']}")

        resp = client.get(f"/api/v1/mappings/{mapping.id}/annotations")
        assert resp.status_code == 200
        bodies = [a["body"] for a in resp.json()]
        assert bodies == ["second"]


class TestDeleteAnnotation:
    def test_author_can_delete_their_own_comment(self, client, mapping):
        created = client.post(f"/api/v1/mappings/{mapping.id}/annotations", json={"body": "delete me"}).json()
        resp = client.delete(f"/api/v1/mappings/{mapping.id}/annotations/{created['id']}")
        assert resp.status_code == 204

    def test_non_author_non_admin_cannot_delete(self, make_client, admin, other_analyst, mapping):
        created = make_client(admin).post(
            f"/api/v1/mappings/{mapping.id}/annotations", json={"body": "admin's comment"},
        ).json()
        resp = make_client(other_analyst).delete(
            f"/api/v1/mappings/{mapping.id}/annotations/{created['id']}",
        )
        assert resp.status_code == 403

    def test_admin_can_delete_anyone_elses_comment(self, make_client, admin, other_analyst, mapping):
        created = make_client(other_analyst).post(
            f"/api/v1/mappings/{mapping.id}/annotations", json={"body": "analyst's comment"},
        ).json()
        resp = make_client(admin).delete(
            f"/api/v1/mappings/{mapping.id}/annotations/{created['id']}",
        )
        assert resp.status_code == 204

    def test_404_for_unknown_annotation(self, client, mapping):
        resp = client.delete(f"/api/v1/mappings/{mapping.id}/annotations/999999")
        assert resp.status_code == 404


class TestStewardCommentOnReviewTransition:
    def test_note_on_transition_creates_a_steward_comment(self, client, mapping):
        client.post(f"/api/v1/mappings/{mapping.id}/review/transition", json={"to_stage": "ai_proposed"})
        resp = client.post(
            f"/api/v1/mappings/{mapping.id}/review/transition",
            json={"to_stage": "pending_review", "note": "Needs a second look at the PII columns"},
        )
        assert resp.status_code == 200

        annotations = client.get(f"/api/v1/mappings/{mapping.id}/annotations").json()
        steward_comments = [a for a in annotations if a["kind"] == "steward_comment"]
        assert len(steward_comments) == 1
        assert steward_comments[0]["review_stage"] == "pending_review"
        assert steward_comments[0]["body"] == "Needs a second look at the PII columns"

    def test_transition_without_a_note_creates_no_comment(self, client, mapping):
        client.post(f"/api/v1/mappings/{mapping.id}/review/transition", json={"to_stage": "ai_proposed"})
        annotations = client.get(f"/api/v1/mappings/{mapping.id}/annotations").json()
        assert annotations == []
