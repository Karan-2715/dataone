"""Celery application instance for async task processing."""

from celery import Celery
from celery.schedules import crontab
from app.core.config import settings
import os

celery_app = Celery(
    "dataone",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    # include lists every module that defines @celery_app.task. Both must be
    # registered or the worker silently drops incoming tasks ("Received
    # unregistered task of name ..."). Schema Mapper requires BOTH:
    # - app.tasks.ai_tasks: schema drift + NL2SQL helpers
    # - app.workers.mapping_tasks: the AI suggestion task (added after the
    #   §11.1 review caught the original omission that made "Get AI
    #   Suggestions" a silent no-op in production).
    include=[
        "app.tasks.ai_tasks",
        "app.tasks.connector_tasks",
        "app.tasks.autopilot_tasks",
        "app.tasks.audit_tasks",
        "app.tasks.schema_intel_tasks",
        "app.tasks.agentic_dba_tasks",
        "app.tasks.aci_tasks",
        "app.workers.mapping_tasks",
        "app.workers.pipeline_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    broker_connection_retry_on_startup=True,
    task_always_eager=os.getenv("DISABLE_CELERY") == "true",
    task_eager_propagates=True,
    beat_schedule={
        "check-schema-drift": {
            "task": "app.tasks.ai_tasks.check_schema_drift_task",
            "schedule": crontab(minute=f"*/{settings.SCHEMA_DRIFT_INTERVAL_MINUTES}"),
        },
        "health-check-all-connections": {
            "task": "app.tasks.connector_tasks.run_all_health_checks",
            "schedule": crontab(minute=f"*/{settings.HEALTH_CHECK_INTERVAL_MINUTES}"),
        },
        # Safety-net sweep; the primary path for the ≤10s NFR is the inline
        # dispatch from the drift/health tasks (ai_autopilot_tasks #5).
        "autopilot-evaluate-recommendations": {
            "task": "app.tasks.autopilot_tasks.evaluate_recommendations_task",
            "schedule": crontab(minute=f"*/{settings.AUTOPILOT_EVALUATE_INTERVAL_MINUTES}"),
        },
        "flush-audit-buffer": {
            "task": "app.tasks.audit_tasks.flush_audit_buffer_task",
            "schedule": crontab(minute=f"*/{settings.AUDIT_BUFFER_FLUSH_INTERVAL_MINUTES}"),
        },
    },
    timezone="UTC",
)
