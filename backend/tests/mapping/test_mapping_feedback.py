"""Tests for the AI Mapping Feedback & Training Loop (Enterprise v2, E14)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app
from app.models.mapping import AISuggestion, Mapping
from app.services.mapping_feedback_service import FeedbackSummaryService, HistoricalMatchService


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
def mapping(db, seeded_connections):
    src, tgt = seeded_connections
    m = Mapping(name="Feedback Test Mapping", source_id=src.id, target_id=tgt.id,
                status="draft", created_by="test")
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _make_suggestion(db, mapping, *, source_column, target_column, status, confidence=80.0):
    s = AISuggestion(
        mapping_id=mapping.id,
        target_table="customers", target_column=target_column,
        source_table="users", source_column=source_column,
        confidence=confidence, status=status,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


class TestHistoricalMatchService:
    def test_no_history_returns_zero_score(self, db):
        result = HistoricalMatchService.lookup(db, "cust_name", "customer_full_name")
        assert result == {"sample_count": 0, "accepted_count": 0, "rejected_count": 0, "score": 0.0}

    def test_prior_acceptance_raises_the_score(self, db, mapping):
        _make_suggestion(db, mapping, source_column="cust_name", target_column="customer_full_name", status="accepted")
        result = HistoricalMatchService.lookup(db, "cust_name", "customer_full_name")
        assert result == {"sample_count": 1, "accepted_count": 1, "rejected_count": 0, "score": 100.0}

    def test_prior_rejection_keeps_the_score_at_zero(self, db, mapping):
        _make_suggestion(db, mapping, source_column="cust_name", target_column="customer_full_name", status="rejected")
        result = HistoricalMatchService.lookup(db, "cust_name", "customer_full_name")
        assert result == {"sample_count": 1, "accepted_count": 0, "rejected_count": 1, "score": 0.0}

    def test_lookup_is_case_insensitive(self, db, mapping):
        _make_suggestion(db, mapping, source_column="Cust_Name", target_column="Customer_Full_Name", status="accepted")
        result = HistoricalMatchService.lookup(db, "cust_name", "customer_full_name")
        assert result["sample_count"] == 1

    def test_pending_suggestions_are_not_counted_as_history(self, db, mapping):
        _make_suggestion(db, mapping, source_column="cust_name", target_column="customer_full_name", status="pending")
        result = HistoricalMatchService.lookup(db, "cust_name", "customer_full_name")
        assert result["sample_count"] == 0

    def test_mixed_history_computes_a_partial_acceptance_rate(self, db, mapping):
        _make_suggestion(db, mapping, source_column="cust_name", target_column="customer_full_name", status="accepted")
        _make_suggestion(db, mapping, source_column="cust_name", target_column="customer_full_name", status="accepted")
        _make_suggestion(db, mapping, source_column="cust_name", target_column="customer_full_name", status="rejected")
        result = HistoricalMatchService.lookup(db, "cust_name", "customer_full_name")
        assert result == {"sample_count": 3, "accepted_count": 2, "rejected_count": 1, "score": 66.7}


class TestFeedbackSummaryService:
    def test_empty_system_returns_zero_summary(self, db):
        summary = FeedbackSummaryService.get_summary(db)
        assert summary == {
            "total_decided": 0, "total_accepted": 0, "total_rejected": 0,
            "acceptance_rate": 0.0, "most_corrected": [],
        }

    def test_computes_acceptance_rate_and_ignores_pending(self, db, mapping):
        _make_suggestion(db, mapping, source_column="a", target_column="a", status="accepted")
        _make_suggestion(db, mapping, source_column="b", target_column="b", status="rejected")
        _make_suggestion(db, mapping, source_column="c", target_column="c", status="pending")
        summary = FeedbackSummaryService.get_summary(db)
        assert summary["total_decided"] == 2
        assert summary["total_accepted"] == 1
        assert summary["total_rejected"] == 1
        assert summary["acceptance_rate"] == 50.0

    def test_most_corrected_only_includes_pairs_with_a_rejection(self, db, mapping):
        _make_suggestion(db, mapping, source_column="clean", target_column="clean", status="accepted")
        _make_suggestion(db, mapping, source_column="messy", target_column="messy", status="rejected")
        _make_suggestion(db, mapping, source_column="messy", target_column="messy", status="rejected")
        summary = FeedbackSummaryService.get_summary(db)
        assert summary["most_corrected"] == [
            {"source_column": "messy", "target_column": "messy", "accepted_count": 0, "rejected_count": 2},
        ]

    def test_most_corrected_is_capped_at_the_limit(self, db, mapping):
        for i in range(15):
            _make_suggestion(db, mapping, source_column=f"col{i}", target_column=f"col{i}", status="rejected")
        summary = FeedbackSummaryService.get_summary(db)
        assert len(summary["most_corrected"]) == 10


class TestFeedbackSummaryApi:
    def test_anonymous_returns_401(self):
        resp = TestClient(app).get("/api/v1/mappings/feedback-summary")
        assert resp.status_code == 401

    def test_returns_live_summary(self, client_admin, db, mapping):
        _make_suggestion(db, mapping, source_column="a", target_column="a", status="accepted")
        resp = client_admin.get("/api/v1/mappings/feedback-summary")
        assert resp.status_code == 200
        assert resp.json()["total_decided"] == 1
        assert resp.json()["acceptance_rate"] == 100.0

    def test_is_registered_before_the_mapping_id_route(self, client_admin):
        """'feedback-summary' must never 422 as an invalid mapping id."""
        resp = client_admin.get("/api/v1/mappings/feedback-summary")
        assert resp.status_code == 200
