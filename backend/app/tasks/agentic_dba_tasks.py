"""Async plan generation for the Agentic DBA Copilot (agentic_dba_tasks #3).

Plan generation grounds in a potentially large catalog and may call the
LLM — it runs as a background task (NFR: never block the chat request),
with the chat UI polling GET /agentic-dba/plans/{id} while status is
"generating".
"""
import logging

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=1, default_retry_delay=15)
def generate_plan_task(self, plan_id: int):
    from app.core.database import SessionLocal
    from app.services.agentic_dba_engine import generate_plan

    logger.info("[pipeline] stage=generate_plan plan_id=%d", plan_id)
    db = SessionLocal()
    try:
        plan = generate_plan(db, plan_id)
        return {"plan_id": plan_id, "status": plan.status}
    except Exception as exc:
        logger.warning("[pipeline] stage=generate_plan failed plan_id=%d error=%s",
                       plan_id, exc)
        raise self.retry(exc=exc)
    finally:
        db.close()
