"""Dynamic Celery beat schedule registration for pipeline schedules (Task #4).

The static ``beat_schedule`` dict in ``celery_app.py`` only supports
schedules known at process start. Pipeline schedules are created/edited at
runtime via the API, so they're registered dynamically here and re-synced
whenever a ``Schedule`` row changes.
"""
import logging

from celery.schedules import crontab
from croniter import croniter

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.pipeline import Schedule

logger = logging.getLogger(__name__)

_SCHEDULE_TASK_PREFIX = "run-pipeline-"


def parse_cron_to_crontab(cron_expression: str) -> crontab:
    """Parse a standard 5-field cron expression into Celery's crontab kwargs.

    Note: ``Schedule.timezone`` is stored but not applied per-entry here —
    Celery's ``beat_schedule`` crontab entries run against the app-wide
    ``celery_app.conf.timezone`` (UTC, per celery_app.py). Per-pipeline
    timezone offsets are a documented follow-up, not silently dropped.
    """
    if not croniter.is_valid(cron_expression):
        raise ValueError(f"invalid cron expression: {cron_expression}")

    parts = cron_expression.strip().split()
    if len(parts) != 5:
        raise ValueError(f"expected 5-field cron expression, got {len(parts)}: {cron_expression}")

    return crontab(
        minute=parts[0],
        hour=parts[1],
        day_of_month=parts[2],
        month_of_year=parts[3],
        day_of_week=parts[4],
    )


def setup_schedule_tasks() -> None:
    """Load all enabled pipeline schedules from the DB and register them
    with Celery beat. Called once at FastAPI startup."""
    db = SessionLocal()
    try:
        schedules = db.query(Schedule).filter(Schedule.enabled == True).all()  # noqa: E712
        registered = 0
        for s in schedules:
            try:
                celery_app.conf.beat_schedule[f"{_SCHEDULE_TASK_PREFIX}{s.pipeline_id}"] = {
                    "task": "app.workers.pipeline_tasks.run_pipeline_task",
                    "schedule": parse_cron_to_crontab(s.cron_expression),
                    "args": (s.pipeline_id,),
                    "kwargs": {"trigger": "scheduled"},
                }
                registered += 1
            except ValueError as e:
                logger.warning("[pipeline] invalid cron for schedule %d: %s", s.id, e)
        logger.info("[pipeline] stage=scheduler_startup registered=%d", registered)
    finally:
        db.close()


def sync_schedule(pipeline_id: int) -> None:
    """Re-sync a single pipeline's beat entry after its Schedule row is
    created/updated/enabled/disabled/deleted."""
    task_name = f"{_SCHEDULE_TASK_PREFIX}{pipeline_id}"
    celery_app.conf.beat_schedule.pop(task_name, None)

    db = SessionLocal()
    try:
        s = db.query(Schedule).filter(
            Schedule.pipeline_id == pipeline_id, Schedule.enabled == True,  # noqa: E712
        ).first()
        if s is None:
            return
        celery_app.conf.beat_schedule[task_name] = {
            "task": "app.workers.pipeline_tasks.run_pipeline_task",
            "schedule": parse_cron_to_crontab(s.cron_expression),
            "args": (pipeline_id,),
            "kwargs": {"trigger": "scheduled"},
        }
        logger.info("[pipeline] stage=schedule_synced pipeline_id=%d", pipeline_id)
    finally:
        db.close()
