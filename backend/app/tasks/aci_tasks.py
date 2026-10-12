"""Async ACI notify-out worker (aci_integration_tasks #5/#9).

Runs the actual external call off the request path (NFR: queuing a
recommendation must never wait on Slack's API). Executes through the
governance registry's `notify_slack_internal` action — the only
auto-capable external action, with its fixed admin-configured destination —
and audits both outcomes (`aci.notify_dispatched` / `aci.notify_failed`) so
the Audit Trail stays the single source of truth without consulting ACI's
own logs.
"""
import logging
from typing import Optional

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task
def notify_out_task(*, event_key: str, title: str, body: str = "",
                    link: Optional[str] = None):
    """Single-shot by design: the ACI client already retries transient
    failures internally, and a notification is fire-and-forget — a second
    Celery-level retry layer would only risk duplicate messages."""
    from app.core.database import SessionLocal
    from app.services.aci_client_service import CircuitBreakerOpen
    from app.services.audit_helper import emit_audit_event
    from app.services.autopilot_registry import check_action_allowed

    logger.info("[pipeline] stage=notify_out event_key=%s", event_key)
    db = SessionLocal()
    try:
        spec = check_action_allowed("notify_slack_internal")
        try:
            result = spec.execute(db, {"title": title, "body": body, "link": link},
                                  "notification-service")
            emit_audit_event(
                db, event_type="aci.notify_dispatched", actor="notification-service",
                module="aci_integration", target_type="notification",
                summary=f"{event_key}: {title[:150]}",
                outcome="success",
                metadata={"event_key": event_key,
                          "action_type": "notify_slack_internal",
                          "destination": result.get("channel"), "link": link},
            )
            db.commit()
            return {"status": "sent", "event_key": event_key}
        except CircuitBreakerOpen as exc:
            # ACI outage: fail fast, no retry storm — audit and stop.
            db.rollback()  # clear any half-applied/poisoned session state first
            _audit_failure(db, emit_audit_event, event_key, title, link, str(exc))
            db.commit()
            return {"status": "failed", "reason": "circuit_open"}
        except Exception as exc:
            db.rollback()  # clear any half-applied/poisoned session state first
            _audit_failure(db, emit_audit_event, event_key, title, link, str(exc))
            db.commit()
            return {"status": "failed", "reason": str(exc)}
    finally:
        db.close()


def _audit_failure(db, emit_audit_event, event_key, title, link, error):
    emit_audit_event(
        db, event_type="aci.notify_failed", actor="notification-service",
        module="aci_integration", target_type="notification",
        summary=f"{event_key}: {title[:150]}",
        outcome="failure",
        metadata={"event_key": event_key,
                  "action_type": "notify_slack_internal",
                  "error": error, "link": link},
    )
