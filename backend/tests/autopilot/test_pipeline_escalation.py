"""Pipeline-failure-autopilot-escalation design: a pipeline whose latest run
is terminally 'failed' escalates into governed Autopilot recommendations
(connector_health_check + notify_slack_internal + optional
pipeline_schedule_disable) instead of just a passive notification.

Mirrors tests/autopilot/test_engine.py's structure/fixtures (db, admin,
two_conns from tests/autopilot/conftest.py). tests/autopilot/conftest.py has
no Pipeline/Mapping fixtures (that lives in tests/pipelines/conftest.py, a
separate suite), so a minimal published-mapping + pipeline builder is
defined locally here, following tests/pipelines/test_pipeline_crud.py's
seeded_connections/seeded_published_mapping pattern.
"""
from app.models.audit import AuditLog
from app.models.autopilot import AutopilotActionLog, AutopilotRecommendation
from app.models.mapping import Mapping, MappingVersion
from app.models.pipeline import PipelineRun
from app.services.autopilot_engine import AutopilotEngine
from app.services.autopilot_service import AutopilotService
from app.services.pipeline_service import PipelineCRUD


def _recs(db, action_type=None, status=None):
    q = db.query(AutopilotRecommendation)
    if action_type:
        q = q.filter(AutopilotRecommendation.action_type == action_type)
    if status:
        q = q.filter(AutopilotRecommendation.status == status)
    return q.all()


def _make_pipeline(db, two_conns, *, name="Escalation Pipeline"):
    """A Pipeline backed by a real published Mapping/MappingVersion, built
    via PipelineCRUD.create_pipeline (the real CRUD path — no schedule
    attached; add one with PipelineCRUD.upsert_schedule as needed)."""
    src, tgt = two_conns
    mapping = Mapping(
        name=f"{name} Map", source_id=src.id, target_id=tgt.id,
        status="published", created_by="test",
    )
    db.add(mapping)
    db.flush()
    version = MappingVersion(
        mapping_id=mapping.id, version_number=1, status="published",
        published_by="test",
        schema_snapshot={"source": {}, "target": {}}, edges_snapshot=[],
    )
    db.add(version)
    db.flush()
    mapping.current_version_id = version.id
    db.commit()

    pipeline = PipelineCRUD.create_pipeline(
        db, name=name,
        source_connection_id=src.id, target_connection_id=tgt.id,
        mapping_id=mapping.id, actor="test",
    )
    db.commit()
    db.refresh(pipeline)
    return pipeline


def _add_run(db, pipeline, *, status, error_message=None, retry_count=0):
    run = PipelineRun(
        pipeline_id=pipeline.id, status=status, trigger="scheduled",
        error_message=error_message, retry_count=retry_count,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_failed_run_with_enabled_schedule_creates_three_recs(db, two_conns):
    pipeline = _make_pipeline(db, two_conns)
    PipelineCRUD.upsert_schedule(
        db, pipeline.id, cron_expression="0 * * * *",
        enabled=True, timezone="UTC", actor="test",
    )
    _add_run(db, pipeline, status="failed",
             error_message="authentication failed", retry_count=3)

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 3

    health = _recs(db, "connector_health_check", "pending")
    assert len(health) == 1
    assert health[0].subject == f"connection:{pipeline.source_connection_id}"

    notify = _recs(db, "notify_slack_internal", "pending")
    assert len(notify) == 1
    assert notify[0].subject == f"pipeline:{pipeline.id}:notify"
    assert pipeline.name in notify[0].payload["title"]
    assert "authentication failed" in notify[0].payload["body"]

    disable = _recs(db, "pipeline_schedule_disable", "pending")
    assert len(disable) == 1
    assert disable[0].subject == f"pipeline:{pipeline.id}"
    assert disable[0].payload == {"pipeline_id": pipeline.id}


def test_failed_run_with_no_schedule_creates_only_two_recs(db, two_conns):
    pipeline = _make_pipeline(db, two_conns)
    _add_run(db, pipeline, status="failed", error_message="boom")

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 2
    assert _recs(db, "pipeline_schedule_disable") == []
    assert len(_recs(db, "connector_health_check", "pending")) == 1
    assert len(_recs(db, "notify_slack_internal", "pending")) == 1


def test_failed_run_with_already_disabled_schedule_creates_only_two_recs(db, two_conns):
    pipeline = _make_pipeline(db, two_conns)
    PipelineCRUD.upsert_schedule(
        db, pipeline.id, cron_expression="0 * * * *",
        enabled=False, timezone="UTC", actor="test",
    )
    _add_run(db, pipeline, status="failed", error_message="boom")

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 2
    assert _recs(db, "pipeline_schedule_disable") == []


def test_reevaluation_refreshes_not_duplicates(db, two_conns):
    pipeline = _make_pipeline(db, two_conns)
    PipelineCRUD.upsert_schedule(
        db, pipeline.id, cron_expression="0 * * * *",
        enabled=True, timezone="UTC", actor="test",
    )
    _add_run(db, pipeline, status="failed", error_message="boom")

    first = AutopilotEngine.evaluate_all(db)
    second = AutopilotEngine.evaluate_all(db)
    assert first["created"] == 3
    assert second["created"] == 0
    assert second["refreshed"] == 3
    assert len(_recs(db, "connector_health_check")) == 1
    assert len(_recs(db, "notify_slack_internal")) == 1
    assert len(_recs(db, "pipeline_schedule_disable")) == 1


def test_only_newest_run_counts_older_failure_then_success_yields_no_rec(db, two_conns):
    """Mirrors test_bug04_only_newest_drift_event_per_connection_is_used:
    an older failed run followed by a newer succeeded run must not trigger
    escalation — only the newest run per pipeline is evaluated."""
    pipeline = _make_pipeline(db, two_conns)
    _add_run(db, pipeline, status="failed", error_message="transient")
    _add_run(db, pipeline, status="succeeded")

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 0
    assert _recs(db, "notify_slack_internal") == []
    assert _recs(db, "pipeline_schedule_disable") == []
    assert _recs(db, "connector_health_check") == []


def test_pipeline_recovers_on_later_run_supersedes_notify_and_disable_recs(db, two_conns):
    pipeline = _make_pipeline(db, two_conns)
    PipelineCRUD.upsert_schedule(
        db, pipeline.id, cron_expression="0 * * * *",
        enabled=True, timezone="UTC", actor="test",
    )
    _add_run(db, pipeline, status="failed", error_message="boom")
    AutopilotEngine.evaluate_all(db)
    assert len(_recs(db, "notify_slack_internal", "pending")) == 1
    assert len(_recs(db, "pipeline_schedule_disable", "pending")) == 1

    _add_run(db, pipeline, status="succeeded")
    counts = AutopilotEngine.evaluate_all(db)
    assert counts["superseded"] == 2

    notify = _recs(db, "notify_slack_internal")[0]
    disable = _recs(db, "pipeline_schedule_disable")[0]
    assert notify.status == "superseded"
    assert disable.status == "superseded"

    audit_reasons = [
        a.payload.get("reason")
        for a in db.query(AuditLog)
        .filter(AuditLog.event_type == "autopilot_recommendation_superseded")
        .all()
    ]
    assert audit_reasons.count("pipeline succeeded on a later run") == 2


def test_approved_schedule_disable_rec_executes_and_disables_the_schedule(db, two_conns):
    """End-to-end approve -> execute_recommendation for pipeline_schedule_disable
    (regression coverage for the bugs/01 fix: _exec_pipeline_schedule_disable
    stages the mutation directly instead of calling the commit-happy,
    HTTPException-raising PipelineCRUD.toggle_schedule, so it must not emit
    a second 'pipeline_schedule_toggled' audit event alongside the executor's
    own 'autopilot_action_executed' one, and must not corrupt the
    execute_recommendation transaction boundary)."""
    pipeline = _make_pipeline(db, two_conns)
    PipelineCRUD.upsert_schedule(
        db, pipeline.id, cron_expression="0 * * * *",
        enabled=True, timezone="UTC", actor="test",
    )
    _add_run(db, pipeline, status="failed", error_message="boom")
    AutopilotEngine.evaluate_all(db)

    rec = _recs(db, "pipeline_schedule_disable", "pending")[0]
    AutopilotService.approve(db, rec.id, actor="admin@test.local")
    out = AutopilotService.execute_recommendation(db, rec.id, auto=False)

    assert out["status"] == "executed"
    db.refresh(rec)
    assert rec.status == "executed"
    assert rec.execution_result == {
        "pipeline_id": pipeline.id, "schedule_disabled": True,
    }

    db.refresh(pipeline.schedule)
    assert pipeline.schedule.enabled is False

    log = (
        db.query(AutopilotActionLog)
        .filter(AutopilotActionLog.recommendation_id == rec.id)
        .one()
    )
    assert log.outcome == "success"

    exec_audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "autopilot_action_executed")
        .all()
    )
    assert len(exec_audit) == 1

    # The executor must NOT go through PipelineCRUD.toggle_schedule's own
    # audit path — only execute_recommendation's single audit event fires.
    toggled_audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "pipeline_schedule_toggled")
        .all()
    )
    assert toggled_audit == []


def test_connector_health_check_draft_merges_with_existing_connector_evaluator(db, two_conns):
    """A pipeline failure and an independently-unhealthy source connection
    both propose connector_health_check on the same subject — only one open
    recommendation row should exist, not two competing ones."""
    src, _ = two_conns
    src.health_status = "down"
    src.last_test_error = "connection refused"
    db.commit()

    pipeline = _make_pipeline(db, two_conns)
    _add_run(db, pipeline, status="failed", error_message="boom")

    counts = AutopilotEngine.evaluate_all(db)
    health_recs = _recs(db, "connector_health_check")
    assert len(health_recs) == 1
    assert health_recs[0].subject == f"connection:{src.id}"
    # Both evaluators drafted it (created once, then the second draft in the
    # same sweep refreshes the just-created row) — never two open rows.
    assert counts["created"] + counts["refreshed"] >= 1


def test_healthy_connection_does_not_get_a_churning_health_check_draft(db, two_conns):
    """Regression: a pipeline can fail for reasons unrelated to connectivity
    (transform/target error, timeout) while its source connection's cached
    health_status is already "healthy" (the common/default case). Drafting
    connector_health_check unconditionally there used to create a rec that
    _supersede_cleared's existing "connection is healthy again" rule closed
    in the very same sweep (same transaction, before the rec was ever
    pending/dispatched) — recreated and re-superseded every subsequent
    sweep, forever, with a fresh notify-out + 2 audit events each time.
    A connection already believed healthy gets no value from this leg, so
    it must not be drafted at all in that case — only the notify + (if
    scheduled) disable legs should fire."""
    src, _ = two_conns
    src.health_status = "healthy"
    db.commit()

    pipeline = _make_pipeline(db, two_conns)
    PipelineCRUD.upsert_schedule(
        db, pipeline.id, cron_expression="0 * * * *",
        enabled=True, timezone="UTC", actor="test",
    )
    _add_run(db, pipeline, status="failed", error_message="target write failed")

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 2
    assert counts["superseded"] == 0
    assert _recs(db, "connector_health_check") == []
    assert len(_recs(db, "notify_slack_internal", "pending")) == 1
    assert len(_recs(db, "pipeline_schedule_disable", "pending")) == 1

    # And it stays quiet on subsequent sweeps too — no oscillating churn.
    counts2 = AutopilotEngine.evaluate_all(db)
    assert counts2["created"] == 0
    assert counts2["superseded"] == 0
    assert _recs(db, "connector_health_check") == []
