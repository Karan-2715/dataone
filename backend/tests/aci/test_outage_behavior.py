"""Outage-behavior verification (aci_integration_tasks #11, AC4): an ACI
outage degrades gracefully at EVERY integration point — this is the wiring
proof, not a re-test of the CircuitBreaker class in isolation."""
from __future__ import annotations

import pytest

from app.core.circuit_breaker import CircuitBreakerOpen
from app.models.autopilot import AutopilotRecommendation
from app.services.aci_client_service import aci_client
from app.services.autopilot_service import AutopilotService
from app.services.notification_service import set_notify_enabled


@pytest.fixture()
def aci_down(fake_aci, monkeypatch):
    monkeypatch.setattr("app.services.aci_client_service.time.sleep", lambda s: None)
    fake_aci.fail_with = ConnectionError("aci unreachable")
    return fake_aci


def _trip_breaker():
    # threshold=3, 2 attempts per call: the second call's retry may already
    # hit the just-opened breaker.
    with pytest.raises(ConnectionError):
        aci_client.search_tools("x")
    with pytest.raises((ConnectionError, CircuitBreakerOpen)):
        aci_client.search_tools("x")


def test_breaker_opens_and_fails_fast_after_threshold(aci_down):
    _trip_breaker()
    with pytest.raises(CircuitBreakerOpen):
        aci_client.search_tools("x")


def test_linked_accounts_endpoint_degrades_to_clear_error(db, aci_down):
    """The integrations page must never 500 because ACI is down."""
    from app.api.routers.integrations import linked_accounts
    _trip_breaker()
    body = linked_accounts(_user=None)
    assert body["accounts"] == []
    assert "circuit open" in body["error"]


def test_recommendation_write_unaffected_by_aci_outage(db, aci_down, monkeypatch):
    """Notify-out during a full ACI outage: the recommendation's own state
    is untouched (the dispatch is enqueued; the WORKER fails and audits)."""
    set_notify_enabled(db, "autopilot:drift_rescan", True, actor="admin@x")
    db.commit()

    dispatched = []

    class _FakeTask:
        @staticmethod
        def delay(**kwargs):
            dispatched.append(kwargs)

    import app.tasks.aci_tasks as aci_tasks
    monkeypatch.setattr(aci_tasks, "notify_out_task", _FakeTask)

    rec, created = AutopilotService.upsert_recommendation(
        db, action_type="drift_rescan", subject="conn-9",
        payload={"connection_id": 9}, rationale={"summary": "drift"},
        confidence=90.0, created_by="autopilot-engine",
    )
    db.commit()
    assert created is True
    persisted = (
        db.query(AutopilotRecommendation)
        .filter(AutopilotRecommendation.subject == "conn-9")
        .one()
    )
    assert persisted.status == "pending"
    assert len(dispatched) == 1  # queued; worker-side failure is audited separately


def test_external_action_chat_request_fails_fast_not_hanging(db, aci_down):
    """Task #4's intent path during an outage: clear error, no hang, and the
    read-query path stays completely unaffected."""
    from app.services.askdata_pipeline_service import _handle_external_action

    _trip_breaker()
    result = {"needs_clarification": False, "error": None, "summary": None,
              "recommendation_id": None}
    out = _handle_external_action(db, "post the findings to #data-governance",
                                  "analyst@x", None, result)
    assert out["error"]
    assert "circuit open" in out["error"]
    assert out["recommendation_id"] is None
    # Nothing was queued while ACI was down.
    assert db.query(AutopilotRecommendation).count() == 0


def test_unconfigured_integration_is_clear_not_cryptic(db, monkeypatch):
    from app.core.config import settings
    from app.services.aci_client_service import AciClientService, aci_client
    from app.services.askdata_pipeline_service import _handle_external_action

    # Real _get_client (no fake fixture used) + unset key + no cached client.
    monkeypatch.setattr(settings, "ACI_API_KEY", None)
    monkeypatch.setattr(aci_client, "_client", None)
    result = {"needs_clarification": False, "error": None, "summary": None,
              "recommendation_id": None}
    out = _handle_external_action(db, "email a@b.com the report", "analyst@x",
                                  None, result)
    assert "isn't configured" in out["error"]
