"""Celery task that drains the durable audit ingestion buffer (AUDIT-T2).

Runs on a beat schedule (see app.core.celery_app). Each buffered event was
held because a direct DB write failed (or the circuit was open) at ingest
time; this task retries them against a fresh session now that the DB may
have recovered, and requeues whatever still fails.
"""
import logging

from app.core.audit_buffer import drain_buffer, requeue
from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.services.audit_helper import _write_audit_row

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.audit_tasks.flush_audit_buffer_task")
def flush_audit_buffer_task() -> dict:
    events = drain_buffer()
    if not events:
        return {"flushed": 0, "requeued": 0, "dropped": 0}

    db = SessionLocal()
    flushed = 0
    still_failing = []
    try:
        for event in events:
            try:
                _write_audit_row(db, **event)
                flushed += 1
            except Exception as exc:
                logger.warning("Flush retry failed for buffered audit event: %s", exc)
                still_failing.append(event)
        db.commit()
    finally:
        db.close()

    dropped = requeue(still_failing) if still_failing else 0
    if flushed or still_failing:
        logger.info(
            "[audit] flush_audit_buffer_task flushed=%d still_failing=%d dropped=%d",
            flushed, len(still_failing), dropped,
        )
    return {"flushed": flushed, "requeued": len(still_failing) - dropped, "dropped": dropped}
