"""Tests for the cron scheduler (Task #4).

Covers: cron expression -> crontab() kwargs translation, invalid cron
rejection, and enabling/disabling a schedule adding/removing the Celery
beat entry via sync_schedule.
"""
import pytest
from celery.schedules import crontab

import app.core.scheduler as scheduler_module
from app.core.scheduler import parse_cron_to_crontab, sync_schedule
from app.services.pipeline_service import PipelineCRUD


class _NoCloseSession:
    """Proxy that hands the scheduler the test session but ignores close()."""

    def __init__(self, s):
        self._s = s

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._s, name)


@pytest.fixture(autouse=True)
def patched_scheduler_env(db, monkeypatch):
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: _NoCloseSession(db))


def test_parse_cron_to_crontab_every_day_at_2am():
    result = parse_cron_to_crontab("0 2 * * *")
    assert isinstance(result, crontab)
    assert result.hour == {2}
    assert result.minute == {0}


def test_parse_cron_to_crontab_every_5_minutes():
    result = parse_cron_to_crontab("*/5 * * * *")
    assert isinstance(result, crontab)
    assert result.minute == set(range(0, 60, 5))


def test_parse_cron_to_crontab_rejects_invalid_expression():
    with pytest.raises(ValueError, match="invalid cron expression"):
        parse_cron_to_crontab("not a cron")


def test_parse_cron_to_crontab_rejects_wrong_field_count():
    # croniter.is_valid() already rejects a 3-field expression, so the
    # explicit 5-field length check in parse_cron_to_crontab is a defensive
    # backstop that this particular input never reaches.
    with pytest.raises(ValueError, match="invalid cron expression"):
        parse_cron_to_crontab("* * *")


def test_upsert_schedule_rejects_invalid_cron(db, admin, seeded_connections, seeded_published_mapping):
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    p = PipelineCRUD.create_pipeline(
        db, name="Sched Test", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.upsert_schedule(
            db, p.id, cron_expression="garbage", enabled=True, timezone="UTC", actor=admin.email,
        )
    assert e.value.status_code == 422


def test_sync_schedule_registers_and_removes_beat_entry(
    db, admin, seeded_connections, seeded_published_mapping,
):
    from app.core.celery_app import celery_app

    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    p = PipelineCRUD.create_pipeline(
        db, name="Sched Beat Test", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
    )
    task_name = f"run-pipeline-{p.id}"
    celery_app.conf.beat_schedule.pop(task_name, None)

    PipelineCRUD.upsert_schedule(
        db, p.id, cron_expression="0 3 * * *", enabled=True, timezone="UTC", actor=admin.email,
    )
    sync_schedule(p.id)
    assert task_name in celery_app.conf.beat_schedule
    assert celery_app.conf.beat_schedule[task_name]["args"] == (p.id,)

    PipelineCRUD.toggle_schedule(db, p.id, enabled=False, actor=admin.email)
    sync_schedule(p.id)
    assert task_name not in celery_app.conf.beat_schedule
