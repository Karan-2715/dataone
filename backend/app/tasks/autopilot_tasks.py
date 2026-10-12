"""Celery tasks for Autopilot governance (ai_autopilot_tasks #5/#6).

Thin wrappers: all logic lives in AutopilotEngine / AutopilotService so
tests exercise the sync cores directly with their own session.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from app.core.celery_app import celery_app
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.autopilot_tasks.evaluate_recommendations_task",
                 bind=True)
def evaluate_recommendations_task(self) -> Dict[str, Any]:
    """Beat-scheduled + event-hook-dispatched trigger evaluation."""
    from app.services.autopilot_engine import AutopilotEngine

    logger.info("[pipeline] stage=autopilot_evaluate_task")
    db = SessionLocal()
    try:
        counts = AutopilotEngine.evaluate_all(db)
        return {"status": "completed", **counts}
    except Exception as exc:
        logger.error("evaluate_recommendations_task failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "failed", "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="app.tasks.autopilot_tasks.execute_recommendation_task",
                 bind=True)
def execute_recommendation_task(self, recommendation_id: int,
                                auto: bool = False) -> Dict[str, Any]:
    """Execute one recommendation through the bounded executor."""
    from app.services.autopilot_service import AutopilotService

    logger.info("[pipeline] stage=autopilot_execute_task rec=%s auto=%s",
                recommendation_id, auto)
    db = SessionLocal()
    try:
        return AutopilotService.execute_recommendation(
            db, recommendation_id, auto=auto,
        )
    except Exception as exc:
        logger.error("execute_recommendation_task failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "failed", "error": str(exc)}
    finally:
        db.close()
