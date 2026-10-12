"""ai_autopilot_tasks #1: the legacy router must never again be reachable
without authentication, and execute mode must never execute directly."""
import pytest

from app.models.autopilot import AutopilotRecommendation


LEGACY_GETS = [
    "/api/v1/autopilot/runs",
    "/api/v1/autopilot/runs/some-run-id/logs",
    "/api/v1/autopilot/runs/some-run-id/status",
]
GOVERNANCE_GETS = [
    "/api/v1/autopilot/policy",
    "/api/v1/autopilot/recommendations",
    "/api/v1/autopilot/actions",
]


@pytest.mark.parametrize("path", LEGACY_GETS + GOVERNANCE_GETS)
def test_get_endpoints_require_auth(client_unauth, path):
    r = client_unauth.get(path)
    assert r.status_code == 401


def test_run_requires_auth(client_unauth):
    r = client_unauth.post(
        "/api/v1/autopilot/run",
        json={"source_id": 1, "target_id": 2, "mode": "suggest"},
    )
    assert r.status_code == 401


def test_viewer_cannot_start_run(client_viewer, two_conns):
    src, tgt = two_conns
    r = client_viewer.post(
        "/api/v1/autopilot/run",
        json={"source_id": src.id, "target_id": tgt.id, "mode": "suggest"},
    )
    assert r.status_code == 403


def test_viewer_can_read_runs(client_viewer):
    assert client_viewer.get("/api/v1/autopilot/runs").status_code == 200


def test_analyst_suggest_run_starts(client_analyst, two_conns, monkeypatch):
    from app.tasks import ai_tasks

    dispatched = {}
    monkeypatch.setattr(
        ai_tasks.run_autopilot_task, "delay",
        lambda **kw: dispatched.update(kw),
    )
    src, tgt = two_conns
    r = client_analyst.post(
        "/api/v1/autopilot/run",
        json={"source_id": src.id, "target_id": tgt.id, "mode": "suggest"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "running"
    assert dispatched["mode"] == "suggest"


def test_execute_mode_is_queued_not_executed(client_admin, db, two_conns, monkeypatch):
    """AC2 via the legacy console: execute mode enters the approval queue."""
    from app.tasks import ai_tasks

    monkeypatch.setattr(
        ai_tasks.run_autopilot_task, "delay",
        lambda **kw: pytest.fail("execute mode must not dispatch the run task directly"),
    )
    src, tgt = two_conns
    r = client_admin.post(
        "/api/v1/autopilot/run",
        json={"source_id": src.id, "target_id": tgt.id, "mode": "execute"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued_for_approval"
    rec = (
        db.query(AutopilotRecommendation)
        .filter(AutopilotRecommendation.id == body["recommendation_id"])
        .one()
    )
    assert rec.action_type == "migration_execute"
    assert rec.status == "pending"
    assert rec.reversible is False

    # A second identical request refreshes the open recommendation (dedupe).
    r2 = client_admin.post(
        "/api/v1/autopilot/run",
        json={"source_id": src.id, "target_id": tgt.id, "mode": "execute"},
    )
    assert r2.status_code == 200
    assert r2.json()["recommendation_id"] == rec.id
    assert r2.json()["already_pending"] is True


def test_invalid_mode_rejected(client_admin, two_conns):
    src, tgt = two_conns
    r = client_admin.post(
        "/api/v1/autopilot/run",
        json={"source_id": src.id, "target_id": tgt.id, "mode": "yolo"},
    )
    assert r.status_code == 422
