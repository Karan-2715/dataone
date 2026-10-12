"""Tests for the Mapping Review & Approval Workflow (Enterprise v2, E13)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app
from app.models.mapping import Mapping
from app.services import schema_service


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
def draft_mapping(db, seeded_connections):
    src, tgt = seeded_connections
    m = Mapping(name="Review Test", source_id=src.id, target_id=tgt.id, status="draft", created_by="test")
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


class TestReviewTransitions:
    def test_new_mapping_starts_in_draft_stage(self, client_admin, draft_mapping):
        resp = client_admin.get(f"/api/v1/mappings/{draft_mapping.id}")
        assert resp.json()["review_stage"] == "draft"

    def test_analyst_can_submit_for_review(self, client_analyst, draft_mapping):
        resp = client_analyst.post(
            f"/api/v1/mappings/{draft_mapping.id}/review/transition",
            json={"to_stage": "pending_review"},
        )
        assert resp.status_code == 200
        assert resp.json()["review_stage"] == "pending_review"

    def test_analyst_cannot_business_approve(self, client_analyst, draft_mapping, db):
        draft_mapping.review_stage = "pending_review"
        db.commit()
        resp = client_analyst.post(
            f"/api/v1/mappings/{draft_mapping.id}/review/transition",
            json={"to_stage": "business_approved"},
        )
        assert resp.status_code == 403

    def test_admin_can_business_approve(self, client_admin, draft_mapping, db):
        draft_mapping.review_stage = "pending_review"
        db.commit()
        resp = client_admin.post(
            f"/api/v1/mappings/{draft_mapping.id}/review/transition",
            json={"to_stage": "business_approved"},
        )
        assert resp.status_code == 200
        assert resp.json()["review_stage"] == "business_approved"

    def test_full_happy_path_to_production_ready(self, client_admin, draft_mapping, db):
        for stage in ("pending_review", "business_approved", "steward_approved", "production_ready"):
            resp = client_admin.post(
                f"/api/v1/mappings/{draft_mapping.id}/review/transition",
                json={"to_stage": stage},
            )
            assert resp.status_code == 200, resp.json()
            assert resp.json()["review_stage"] == stage

    def test_production_ready_mapping_can_be_sent_back_to_draft_for_revision(
        self, client_admin, draft_mapping, db,
    ):
        """build-validation B-E13-08: a production mapping needing
        revision (e.g. a source column type changed) must have a way
        back into the workflow instead of being stuck forever."""
        for stage in ("pending_review", "business_approved", "steward_approved", "production_ready"):
            client_admin.post(f"/api/v1/mappings/{draft_mapping.id}/review/transition", json={"to_stage": stage})

        resp = client_admin.post(
            f"/api/v1/mappings/{draft_mapping.id}/review/transition",
            json={"to_stage": "draft"},
        )
        assert resp.status_code == 200
        assert resp.json()["review_stage"] == "draft"

    def test_analyst_can_also_send_a_production_mapping_back_to_draft(
        self, client_admin, draft_mapping, analyst,
    ):
        for stage in ("pending_review", "business_approved", "steward_approved", "production_ready"):
            client_admin.post(f"/api/v1/mappings/{draft_mapping.id}/review/transition", json={"to_stage": stage})

        # client_admin and client_analyst both wrap the same `app` and share
        # its global dependency_overrides — requesting both fixtures in one
        # test would have the later one silently clobber the earlier one's
        # user override, so switch the override on the existing client
        # in-place instead of pulling in client_analyst.
        app.dependency_overrides[get_current_user] = lambda: analyst
        resp = client_admin.post(
            f"/api/v1/mappings/{draft_mapping.id}/review/transition",
            json={"to_stage": "draft"},
        )
        assert resp.status_code == 200

    def test_cannot_skip_stages(self, client_admin, draft_mapping):
        resp = client_admin.post(
            f"/api/v1/mappings/{draft_mapping.id}/review/transition",
            json={"to_stage": "production_ready"},
        )
        assert resp.status_code == 409

    def test_unknown_stage_returns_422(self, client_admin, draft_mapping):
        resp = client_admin.post(
            f"/api/v1/mappings/{draft_mapping.id}/review/transition",
            json={"to_stage": "not_a_real_stage"},
        )
        assert resp.status_code == 422

    def test_rejected_mapping_can_be_resubmitted_to_draft(self, client_admin, draft_mapping, db):
        draft_mapping.review_stage = "pending_review"
        db.commit()
        client_admin.post(f"/api/v1/mappings/{draft_mapping.id}/review/transition", json={"to_stage": "rejected"})
        resp = client_admin.post(f"/api/v1/mappings/{draft_mapping.id}/review/transition", json={"to_stage": "draft"})
        assert resp.status_code == 200
        assert resp.json()["review_stage"] == "draft"

    def test_transition_does_not_touch_status_or_publish_state(self, client_admin, draft_mapping):
        client_admin.post(f"/api/v1/mappings/{draft_mapping.id}/review/transition", json={"to_stage": "pending_review"})
        resp = client_admin.get(f"/api/v1/mappings/{draft_mapping.id}")
        assert resp.json()["status"] == "draft"  # untouched by the review workflow
        assert resp.json()["current_version_id"] is None

    def test_transition_is_audited(self, client_admin, draft_mapping, db):
        client_admin.post(f"/api/v1/mappings/{draft_mapping.id}/review/transition", json={"to_stage": "pending_review"})
        from app.models.audit import AuditLog
        events = db.query(AuditLog).filter(AuditLog.event_type == "mapping.review_transitioned").all()
        assert len(events) == 1


class TestReviewQueue:
    def test_viewer_forbidden(self, client_viewer):
        assert client_viewer.get("/api/v1/mappings/review-queue").status_code == 403

    def test_queue_excludes_draft_and_production_ready_by_default(self, client_admin, draft_mapping):
        client_admin.post(f"/api/v1/mappings/{draft_mapping.id}/review/transition", json={"to_stage": "pending_review"})
        resp = client_admin.get("/api/v1/mappings/review-queue")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == draft_mapping.id

    def test_queue_filters_by_specific_stage(self, client_admin, draft_mapping):
        client_admin.post(f"/api/v1/mappings/{draft_mapping.id}/review/transition", json={"to_stage": "pending_review"})
        resp = client_admin.get("/api/v1/mappings/review-queue?stage=business_approved")
        assert resp.json()["total"] == 0

    def test_draft_mapping_not_in_default_queue(self, client_admin, draft_mapping):
        resp = client_admin.get("/api/v1/mappings/review-queue")
        assert resp.json()["total"] == 0

    def test_draft_mapping_is_still_discoverable_via_explicit_stage_filter(self, client_admin, draft_mapping):
        """build-validation B-E13-09: excluding draft from the *default*
        queue is deliberate (see get_queue's docstring) — this proves the
        explicit escape hatch it relies on actually works, so a draft is
        never truly invisible."""
        resp = client_admin.get("/api/v1/mappings/review-queue?stage=draft")
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["id"] == draft_mapping.id

    def test_draft_mapping_is_discoverable_via_the_plain_mapping_list(self, client_admin, draft_mapping):
        resp = client_admin.get("/api/v1/mappings/")
        ids = [m["id"] for m in resp.json()["items"]]
        assert draft_mapping.id in ids
