"""Celery task that executes a pipeline run asynchronously (Task #3),
with per-pipeline retry-on-transient-failure (Task #5).

Called by the manual run endpoint (POST /pipelines/{id}/run), the re-run
endpoint (POST /pipelines/{id}/runs/{run_id}/rerun), and the cron
scheduler (app.core.scheduler).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.pipeline import PipelineRun, RetryPolicy
from app.services.pipeline_executor import PipelineExecutor

logger = logging.getLogger(__name__)

# Failures matching these substrings (case-insensitive) are treated as
# transient and retried. Anything else defaults to retryable too (Task #5
# Decision: unknown errors are retried rather than silently failing a run
# that might recover) EXCEPT the terminal patterns below, which are
# deterministic and would fail identically on every retry.
RETRYABLE_ERROR_SUBSTRINGS = [
    "timeout", "connection refused", "connection reset", "deadlock",
    "too many connections", "rate limit", "temporary failure",
    "could not connect", "lock wait timeout", "transaction rollback",
]
TERMINAL_ERROR_SUBSTRINGS = [
    "blocked by schema drift", "authentication failed", "access denied",
    "permission denied", "syntax error", "does not exist", "not found",
    "malformed", "constraint failed", "unique constraint",
    "not yet supported", "only supports 'direct'", "no field mappings",
]


def classify_error(error_message: str) -> str:
    """Classify a failure as 'retryable' or 'terminal'. Unknown errors
    default to 'retryable' (conservative — retry rather than silently
    fail a run that might recover)."""
    lowered = (error_message or "").lower()
    for pattern in TERMINAL_ERROR_SUBSTRINGS:
        if pattern in lowered:
            return "terminal"
    for pattern in RETRYABLE_ERROR_SUBSTRINGS:
        if pattern in lowered:
            return "retryable"
    return "retryable"


@celery_app.task(
    name="app.workers.pipeline_tasks.run_pipeline_task",
    bind=True,
    autoretry_for=(),  # retry decision is made explicitly below, not via autoretry_for
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def run_pipeline_task(self, pipeline_id: int, run_id: int, trigger: str = "manual") -> Dict[str, Any]:
    """Execute a pipeline asynchronously with configurable per-pipeline retry."""
    result = PipelineExecutor.execute(pipeline_id, run_id, trigger)

    if result.get("status") != "failed":
        return result

    error_msg = result.get("error", "")
    classification = classify_error(error_msg)
    if classification == "terminal":
        logger.info("[pipeline] run_id=%s terminal failure, not retrying: %s", run_id, error_msg)
        return {**result, "retries_exhausted": True}

    db = SessionLocal()
    try:
        retry_policy = db.query(RetryPolicy).filter(RetryPolicy.pipeline_id == pipeline_id).first()
        max_attempts = retry_policy.max_attempts if retry_policy else 3
        backoff_seconds = retry_policy.backoff_seconds if retry_policy else 60
    finally:
        db.close()

    if self.request.retries >= max_attempts - 1:
        logger.info("[pipeline] run_id=%s retries exhausted (%d/%d)",
                     run_id, self.request.retries + 1, max_attempts)
        return {**result, "retries_exhausted": True}

    _mark_retrying(run_id, self.request.retries + 1, error_msg)
    logger.info("[pipeline] run_id=%s retryable failure, retry %d/%d in %ds: %s",
                run_id, self.request.retries + 1, max_attempts, backoff_seconds, error_msg)
    raise self.retry(countdown=backoff_seconds, max_retries=max_attempts - 1)


def _mark_retrying(run_id: int, retry_count: int, error_msg: str) -> None:
    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run:
            run.status = "retrying"
            run.retry_count = retry_count
            run.error_message = f"Attempt {retry_count}: {error_msg}"
            db.commit()
    finally:
        db.close()
