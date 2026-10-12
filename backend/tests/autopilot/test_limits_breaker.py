"""ai_autopilot_tasks #7: rate limits + circuit breaker (auto path only)."""
from datetime import datetime, timezone

from app.models.audit import AuditLog
from app.models.autopilot import AutopilotActionLog, AutopilotRecommendation
from app.services.autopilot_service import AutopilotService


def _seed_auto_log(db, action_type: str, outcome: str, n: int = 1):
    for _ in range(n):
        db.add(AutopilotActionLog(
            action_type=action_type, payload={}, mode="auto", outcome=outcome,
            actor="autopilot-policy", started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        ))
    db.commit()


def _make_auto_rec(db, client_admin, src):
    client_admin.put(
        "/api/v1/autopilot/policy/connector_health_check",
        json={"autonomy": "auto", "max_auto_per_hour": 3},
    )
    rec, _ = AutopilotService.upsert_recommendation(
        db, action_type="connector_health_check",
        subject=f"connection:{src.id}", payload={"connection_id": src.id},
        rationale={"summary": "s", "evidence": [], "trigger": {}},
        confidence=90.0, created_by="autopilot-engine",
    )
    db.commit()
    return rec


def test_per_type_limit_demotes(db, client_admin, two_conns):
    src, _ = two_conns
    rec = _make_auto_rec(db, client_admin, src)
    _seed_auto_log(db, "connector_health_check", "success", n=3)  # limit = 3

    out = AutopilotService.execute_recommendation(db, rec.id, auto=True)
    assert out["status"] == "demoted"
    assert "per-type limit" in out["reason"]
    db.refresh(rec)
    assert rec.status == "pending"  # back in the human queue, never dropped
    blocked = (
        db.query(AutopilotActionLog)
        .filter(AutopilotActionLog.outcome == "blocked_rate_limit")
        .one()
    )
    assert blocked.recommendation_id == rec.id
    assert (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "autopilot_rate_limited")
        .count()
        == 1
    )


def test_global_limit_demotes(db, client_admin, two_conns, monkeypatch):
    from app.core.config import settings

    src, _ = two_conns
    rec = _make_auto_rec(db, client_admin, src)
    monkeypatch.setattr(settings, "AUTOPILOT_GLOBAL_AUTO_LIMIT_PER_HOUR", 2)
    # Volume on a DIFFERENT action type still counts globally.
    _seed_auto_log(db, "drift_rescan", "success", n=2)

    out = AutopilotService.execute_recommendation(db, rec.id, auto=True)
    assert out["status"] == "demoted"
    assert "global limit" in out["reason"]


def test_breaker_opens_after_consecutive_failures(db, client_admin, two_conns):
    src, _ = two_conns
    rec = _make_auto_rec(db, client_admin, src)
    _seed_auto_log(db, "connector_health_check", "failure", n=3)

    assert AutopilotService.breaker_open(db, "connector_health_check") is True
    out = AutopilotService.execute_recommendation(db, rec.id, auto=True)
    assert out["status"] == "demoted"
    assert "circuit breaker" in out["reason"]
    assert (
        db.query(AutopilotActionLog)
        .filter(AutopilotActionLog.outcome == "blocked_breaker")
        .count()
        == 1
    )
    assert (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "autopilot_circuit_breaker_open")
        .count()
        == 1
    )


def test_breaker_closes_after_a_success(db):
    _seed_auto_log(db, "connector_health_check", "failure", n=3)
    _seed_auto_log(db, "connector_health_check", "success", n=1)
    assert AutopilotService.breaker_open(db, "connector_health_check") is False


def test_breaker_ignores_other_types_and_human_mode(db):
    _seed_auto_log(db, "drift_rescan", "failure", n=3)
    assert AutopilotService.breaker_open(db, "connector_health_check") is False
    # Human-approved failures never open the auto breaker.
    for _ in range(3):
        db.add(AutopilotActionLog(
            action_type="connector_health_check", payload={}, mode="approved",
            outcome="failure", actor="admin@test.local",
            started_at=datetime.now(timezone.utc),
        ))
    db.commit()
    assert AutopilotService.breaker_open(db, "connector_health_check") is False


def test_human_approvals_not_rate_limited(db, client_admin, two_conns):
    """Limits bound autonomy, not humans: approved path executes even when
    the auto counters are exhausted."""
    src, _ = two_conns
    rec = _make_auto_rec(db, client_admin, src)
    _seed_auto_log(db, "connector_health_check", "success", n=10)

    AutopilotService.approve(db, rec.id, actor="admin@test.local")
    out = AutopilotService.execute_recommendation(db, rec.id, auto=False)
    assert out["status"] == "executed"


def test_demoted_rec_can_then_be_human_approved(db, client_admin, two_conns):
    src, _ = two_conns
    rec = _make_auto_rec(db, client_admin, src)
    _seed_auto_log(db, "connector_health_check", "success", n=3)
    assert AutopilotService.execute_recommendation(db, rec.id, auto=True)["status"] == "demoted"

    AutopilotService.approve(db, rec.id, actor="admin@test.local")
    out = AutopilotService.execute_recommendation(db, rec.id, auto=False)
    assert out["status"] == "executed"
    db.refresh(rec)
    assert rec.status == "executed"


# ── bugs/03 + bugs/05: demotion staleness + structured block reasons ─────


def test_bug03_demoted_rec_object_is_fresh_without_manual_refresh(
    db, client_admin, two_conns,
):
    """bugs/03: after a demotion the in-memory object must read 'pending',
    not the stale 'executing' the bulk UPDATE bypassed the identity map with."""
    src, _ = two_conns
    rec = _make_auto_rec(db, client_admin, src)
    _seed_auto_log(db, "connector_health_check", "success", n=3)

    out = AutopilotService.execute_recommendation(db, rec.id, auto=True)
    assert out["status"] == "demoted"
    # No db.refresh(rec) here — that's the point.
    assert rec.status == "pending"
    assert rec.decided_by is None


def test_bug05_blocked_detail_has_structured_blocked_by(
    db, client_admin, two_conns,
):
    """bugs/05: audit queries filter on detail->>'blocked_by', never on the
    human-readable reason text."""
    src, _ = two_conns

    # rate limit
    rec1 = _make_auto_rec(db, client_admin, src)
    _seed_auto_log(db, "connector_health_check", "success", n=3)
    AutopilotService.execute_recommendation(db, rec1.id, auto=True)
    row = (
        db.query(AutopilotActionLog)
        .filter(AutopilotActionLog.outcome == "blocked_rate_limit")
        .one()
    )
    assert row.detail["blocked_by"] == "rate_limit"
    assert "reason" in row.detail
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "autopilot_rate_limited")
        .one()
    )
    assert audit.payload["blocked_by"] == "rate_limit"

    # breaker
    _seed_auto_log(db, "drift_rescan", "failure", n=3)
    client_admin.put(
        "/api/v1/autopilot/policy/drift_rescan",
        json={"autonomy": "auto", "max_auto_per_hour": 100},
    )
    rec2, _ = AutopilotService.upsert_recommendation(
        db, action_type="drift_rescan",
        subject=f"connection:{src.id}", payload={"connection_id": src.id},
        rationale={"summary": "s", "evidence": [], "trigger": {}},
        confidence=90.0, created_by="autopilot-engine",
    )
    db.commit()
    AutopilotService.execute_recommendation(db, rec2.id, auto=True)
    row = (
        db.query(AutopilotActionLog)
        .filter(AutopilotActionLog.outcome == "blocked_breaker")
        .one()
    )
    assert row.detail["blocked_by"] == "breaker"
