"""Tests for retry/failure classification (Task #5) and rerun +
concurrency guard (Tasks #6, #9).

run_pipeline_task itself dispatches through Celery's retry machinery,
which needs a running worker/broker to exercise end-to-end — that's
flagged as a manual QA step in the task spec. Here we test the pieces
that don't require Celery: error classification, and the concurrency /
rerun logic in PipelineCRUD.create_run.
"""
import pytest
from fastapi import HTTPException

from app.models.pipeline import PipelineRun
from app.services.pipeline_service import PipelineCRUD
from app.workers.pipeline_tasks import classify_error


@pytest.mark.parametrize("message", [
    "connection timeout after 30s",
    "connection refused by host",
    "deadlock detected",
    "too many connections",
    "lock wait timeout exceeded",
])
def test_classify_error_retryable(message):
    assert classify_error(message) == "retryable"


@pytest.mark.parametrize("message", [
    "blocked by schema drift: source schema has changed",
    "authentication failed for user",
    "permission denied for table x",
    "syntax error near SELECT",
    "unique constraint failed: customers.cust_id",
])
def test_classify_error_terminal(message):
    assert classify_error(message) == "terminal"


def test_classify_error_unknown_defaults_to_retryable():
    assert classify_error("something completely unexpected happened") == "retryable"


def _make_pipeline(db, admin, seeded_connections, seeded_published_mapping):
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    return PipelineCRUD.create_pipeline(
        db, name="Retry Test", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
    )


def test_create_pipeline_gets_default_retry_policy(db, admin, seeded_connections, seeded_published_mapping):
    p = _make_pipeline(db, admin, seeded_connections, seeded_published_mapping)
    db.refresh(p)
    assert p.retry_policy is not None
    assert p.retry_policy.max_attempts == 3
    assert p.retry_policy.backoff_seconds == 60


def test_upsert_retry_policy_overrides_default(db, admin, seeded_connections, seeded_published_mapping):
    p = _make_pipeline(db, admin, seeded_connections, seeded_published_mapping)
    policy = PipelineCRUD.upsert_retry_policy(
        db, p.id, max_attempts=5, backoff_seconds=120,
        retryable_error_patterns=["timeout"], actor=admin.email,
    )
    assert policy.max_attempts == 5
    assert policy.backoff_seconds == 120
    assert policy.retryable_error_patterns == ["timeout"]


def test_create_run_blocks_second_concurrent_run(db, admin, seeded_connections, seeded_published_mapping):
    p = _make_pipeline(db, admin, seeded_connections, seeded_published_mapping)
    PipelineCRUD.create_run(db, p.id, trigger="manual", actor=admin.email)

    with pytest.raises(HTTPException) as e:
        PipelineCRUD.create_run(db, p.id, trigger="manual", actor=admin.email)
    assert e.value.status_code == 409


def test_create_run_allowed_after_previous_completes(db, admin, seeded_connections, seeded_published_mapping):
    p = _make_pipeline(db, admin, seeded_connections, seeded_published_mapping)
    run1 = PipelineCRUD.create_run(db, p.id, trigger="manual", actor=admin.email)
    run1.status = "succeeded"
    db.commit()

    run2 = PipelineCRUD.create_run(db, p.id, trigger="manual", actor=admin.email)
    assert run2.id != run1.id


def test_create_run_rejects_disabled_pipeline(db, admin, seeded_connections, seeded_published_mapping):
    p = _make_pipeline(db, admin, seeded_connections, seeded_published_mapping)
    PipelineCRUD.update_pipeline(db, p.id, name=None, enabled=False, actor=admin.email)

    with pytest.raises(HTTPException) as e:
        PipelineCRUD.create_run(db, p.id, trigger="manual", actor=admin.email)
    assert e.value.status_code == 422


def test_create_run_rerun_sets_parent_run_id(db, admin, seeded_connections, seeded_published_mapping):
    p = _make_pipeline(db, admin, seeded_connections, seeded_published_mapping)
    run1 = PipelineCRUD.create_run(db, p.id, trigger="manual", actor=admin.email)
    run1.status = "succeeded"
    db.commit()

    rerun = PipelineCRUD.create_run(
        db, p.id, trigger="rerun", actor=admin.email, parent_run_id=run1.id,
    )
    assert rerun.parent_run_id == run1.id
    assert rerun.trigger == "rerun"


def test_get_run_404_for_wrong_pipeline(db, admin, seeded_connections, seeded_published_mapping):
    p = _make_pipeline(db, admin, seeded_connections, seeded_published_mapping)
    run = PipelineCRUD.create_run(db, p.id, trigger="manual", actor=admin.email)

    with pytest.raises(HTTPException) as e:
        PipelineCRUD.get_run(db, p.id, run.id + 999)
    assert e.value.status_code == 404


def test_list_runs_filters_by_status_and_trigger(db, admin, seeded_connections, seeded_published_mapping):
    p = _make_pipeline(db, admin, seeded_connections, seeded_published_mapping)
    run1 = PipelineCRUD.create_run(db, p.id, trigger="manual", actor=admin.email)
    run1.status = "succeeded"
    db.commit()
    run2 = PipelineCRUD.create_run(db, p.id, trigger="scheduled", actor=admin.email)
    run2.status = "failed"
    db.commit()

    items, total = PipelineCRUD.list_runs(db, p.id, status="succeeded")
    assert total == 1
    assert items[0].id == run1.id

    items, total = PipelineCRUD.list_runs(db, p.id, trigger="scheduled")
    assert total == 1
    assert items[0].id == run2.id
