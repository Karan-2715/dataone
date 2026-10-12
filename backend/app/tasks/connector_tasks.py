"""Periodic connection health checks (connector_tasks #5, TRD FR5).

Beat dispatches run_all_health_checks every HEALTH_CHECK_INTERVAL_MINUTES;
it fans out one rate-limited task per non-deleted connection so checks
parallelize across workers without stampeding the target databases.
"""
import logging

from app.core.celery_app import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)


@celery_app.task
def run_all_health_checks():
    """Dispatch one health-check task per non-deleted connection."""
    from app.core.database import SessionLocal
    from app.models.connection import DBConnection

    logger.info("[pipeline] stage=health_check_dispatch")
    db = SessionLocal()
    try:
        connection_ids = [
            row.id for row in
            db.query(DBConnection.id)
            .filter(DBConnection.is_deleted == False)  # noqa: E712
            .all()
        ]
    finally:
        db.close()

    for cid in connection_ids:
        run_health_check_for_connection.delay(cid)

    logger.info("[pipeline] stage=health_check_dispatch dispatched=%d",
                len(connection_ids))
    return {"dispatched": len(connection_ids)}


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60,
                 rate_limit=settings.HEALTH_CHECK_RATE_LIMIT)
def run_health_check_for_connection(self, connection_id: int):
    """Test a single connection and persist its health status."""
    from app.core.database import SessionLocal
    from app.models.connection import DBConnection
    from app.services.connection_service import ConnectionService
    from app.services.schema_service import SchemaService

    logger.info("[pipeline] stage=health_check connection_id=%s", connection_id)
    db = SessionLocal()
    try:
        conn = (
            db.query(DBConnection)
            .filter(DBConnection.id == connection_id,
                    DBConnection.is_deleted == False)  # noqa: E712
            .first()
        )
        if not conn:
            return {"status": "skipped", "reason": "connection deleted or not found"}

        if conn.secrets_ref:
            # Secret-manager integration (connector_tasks #2) hasn't landed;
            # a populated secrets_ref means config alone can't authenticate.
            logger.warning(
                "[pipeline] stage=health_check connection_id=%s has secrets_ref "
                "but no secret manager is configured — testing with config only",
                connection_id,
            )

        # SchemaService.test_connection owns the timeout + never raises.
        result = SchemaService.test_connection(conn)
        if result.success:
            ConnectionService.update_health(db, connection_id, "healthy")
        elif result.reachable:
            ConnectionService.update_health(db, connection_id, "degraded",
                                            result.error_message)
        else:
            ConnectionService.update_health(db, connection_id, "down",
                                            result.error_message)
        db.commit()

        if not result.success:
            # Autopilot ≤10s NFR: an unhealthy result is a trigger — evaluate
            # now instead of waiting for the beat sweep. Guarded so a dispatch
            # failure never breaks the health check itself.
            try:
                from app.tasks.autopilot_tasks import evaluate_recommendations_task
                evaluate_recommendations_task.delay()
            except Exception as exc:
                logger.warning(
                    "autopilot evaluate dispatch after health check failed: %s", exc,
                )

        return {
            "status": "completed",
            "connection_id": connection_id,
            "success": result.success,
            "error_code": result.error_code,
        }
    except Exception as e:
        db.rollback()
        logger.error("[pipeline] stage=health_check connection_id=%s failed: %s",
                     connection_id, e)
        try:
            ConnectionService.update_health(db, connection_id, "down", str(e))
            db.commit()
        except Exception:
            db.rollback()
        raise self.retry(exc=e)
    finally:
        db.close()
