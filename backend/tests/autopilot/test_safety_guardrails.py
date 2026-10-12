"""ai_autopilot_tasks #10: TRD §7 acceptance criteria as executable tests.

These deliberately overlap the per-task unit tests: they are the safety
contract, phrased end-to-end, and must keep passing even if internals are
refactored.
"""
from app.models.audit import AuditLog
from app.models.autopilot import (
    AutopilotActionLog,
    AutopilotPolicy,
    AutopilotRecommendation,
)
from app.services.autopilot_engine import AutopilotEngine
from app.services.autopilot_service import AutopilotService


def _dispatch_calls(monkeypatch):
    from app.tasks import autopilot_tasks

    calls = []
    monkeypatch.setattr(
        autopilot_tasks.execute_recommendation_task, "delay",
        lambda **kw: calls.append(kw),
    )
    return calls


def test_ac1_suggest_only_is_never_executed(db, two_conns, monkeypatch):
    """AC1: action type at 'suggest' ⇒ recommendation appears, nothing runs."""
    calls = _dispatch_calls(monkeypatch)
    src, _ = two_conns
    src.health_status = "down"
    db.commit()

    counts = AutopilotEngine.evaluate_all(db)  # default policy is suggest
    assert counts["created"] == 1
    assert counts["auto_dispatched"] == 0
    assert calls == []
    rec = db.query(AutopilotRecommendation).one()
    assert rec.status == "pending"
    assert db.query(AutopilotActionLog).count() == 0


def test_ac2_approval_gate(db, two_conns, pending_health_rec):
    """AC2: above-threshold action executes only after explicit approval."""
    # A stray direct dispatch on a still-pending rec must refuse.
    out = AutopilotService.execute_recommendation(
        db, pending_health_rec.id, auto=False,
    )
    assert out["status"] == "skipped"
    assert db.query(AutopilotActionLog).count() == 0

    AutopilotService.approve(db, pending_health_rec.id, actor="admin@test.local")
    out = AutopilotService.execute_recommendation(
        db, pending_health_rec.id, auto=False,
    )
    assert out["status"] == "executed"


def test_ac3_prohibited_hard_block_regardless_of_config(db):
    """AC3: even with a policy row forced to 'auto' directly in the DB
    (bypassing the API's 422) and a recommendation forged for a prohibited
    action, execution is hard-blocked at the service layer."""
    db.add(AutopilotPolicy(action_type="mapping_publish", autonomy="auto"))
    rec = AutopilotRecommendation(
        action_type="mapping_publish",
        payload={"mapping_id": 1},
        subject="mapping:1",
        dedupe_key="mapping_publish:mapping:1",
        rationale={"summary": "forged"},
        confidence=100.0,
        risk="high",
        reversible=False,
        status="pending",
        created_by="attacker",
    )
    db.add(rec)
    db.commit()

    out = AutopilotService.execute_recommendation(db, rec.id, auto=True)
    assert out["status"] == "blocked_prohibited"
    assert "regardless of policy" in out["error"]
    db.refresh(rec)
    assert rec.status == "failed"
    log = db.query(AutopilotActionLog).one()
    assert log.outcome == "blocked_prohibited"
    assert (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "autopilot_action_blocked")
        .count()
        == 1
    )


def test_ac3_unknown_action_default_denied(db):
    rec = AutopilotRecommendation(
        action_type="rm_rf_production",
        payload={},
        subject="everything",
        dedupe_key="rm_rf_production:everything",
        rationale={"summary": "forged"},
        confidence=100.0,
        risk="low",
        reversible=True,
        status="pending",
        created_by="attacker",
    )
    db.add(rec)
    db.commit()
    out = AutopilotService.execute_recommendation(db, rec.id, auto=True)
    assert out["status"] == "blocked_prohibited"
    assert "allow-list" in out["error"]


def test_ac3_irreversible_action_never_auto_even_if_policy_forced(db, two_conns):
    """migration_execute forced to 'auto' in the DB: the executor's
    auto-capability clamp demotes it to the human queue."""
    src, tgt = two_conns
    db.add(AutopilotPolicy(action_type="migration_execute", autonomy="auto"))
    rec, _ = AutopilotService.upsert_recommendation(
        db, action_type="migration_execute",
        subject=f"migration:{src.id}->{tgt.id}",
        payload={"source_id": src.id, "target_id": tgt.id},
        rationale={"summary": "s", "evidence": [], "trigger": {}},
        confidence=100.0, created_by="someone",
    )
    db.commit()

    out = AutopilotService.execute_recommendation(db, rec.id, auto=True)
    assert out["status"] == "demoted"
    db.refresh(rec)
    assert rec.status == "pending"  # waiting for a human, not executed
    assert db.query(AutopilotActionLog).one().outcome == "blocked_policy"


def test_ac4_bounded_autonomous_execution(db, client_admin, two_conns, monkeypatch):
    """AC4: allow-listed, reversible, low-risk action with policy 'auto'
    executes on trigger and logs rationale + outcome, end to end."""
    calls = _dispatch_calls(monkeypatch)
    src, _ = two_conns
    client_admin.put(
        "/api/v1/autopilot/policy/connector_health_check",
        json={"autonomy": "auto"},
    )
    src.health_status = "down"
    db.commit()

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["auto_dispatched"] == 1
    assert len(calls) == 1 and calls[0]["auto"] is True

    # Run what the worker would run.
    out = AutopilotService.execute_recommendation(
        db, calls[0]["recommendation_id"], auto=True,
    )
    assert out["status"] == "executed"
    rec = (
        db.query(AutopilotRecommendation)
        .filter(AutopilotRecommendation.id == calls[0]["recommendation_id"])
        .one()
    )
    assert rec.status == "executed"
    assert rec.decision_mode == "auto"
    assert rec.rationale["summary"]  # rationale persisted with the action
    log = db.query(AutopilotActionLog).one()
    assert log.mode == "auto"
    assert log.outcome == "success"
    assert log.reversibility_note
    # Full audit trail: created → executed.
    events = [
        a.event_type for a in
        db.query(AuditLog).order_by(AuditLog.id.asc()).all()
    ]
    assert "autopilot_recommendation_created" in events
    assert "autopilot_action_executed" in events


def test_policy_disabled_supersedes_instead_of_queueing(db, client_admin, two_conns,
                                                        monkeypatch):
    calls = _dispatch_calls(monkeypatch)
    src, _ = two_conns
    client_admin.put(
        "/api/v1/autopilot/policy/connector_health_check",
        json={"autonomy": "disabled"},
    )
    src.health_status = "down"
    db.commit()

    AutopilotEngine.evaluate_all(db)
    assert calls == []
    rec = db.query(AutopilotRecommendation).one()
    assert rec.status == "superseded"


def test_full_lifecycle_audit_order(db, client_admin, pending_health_rec, monkeypatch):
    """ai_autopilot_tasks #8: created → approved → executed appear in order."""
    _dispatch_calls(monkeypatch)
    client_admin.post(
        f"/api/v1/autopilot/recommendations/{pending_health_rec.id}/approve",
    )
    AutopilotService.execute_recommendation(db, pending_health_rec.id, auto=False)
    events = [
        a.event_type for a in db.query(AuditLog).order_by(AuditLog.id.asc()).all()
        if a.event_type.startswith("autopilot_")
    ]
    created = events.index("autopilot_recommendation_created")
    approved = events.index("autopilot_recommendation_approved")
    executed = events.index("autopilot_action_executed")
    assert created < approved < executed
