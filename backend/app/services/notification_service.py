"""Notify-out fan-out service (aci_integration_tasks #5/#7).

One shared implementation for every trigger point (Autopilot approval
queue, Agentic DBA plans, pipeline runs/drift): check the per-event-type
opt-in flag, then FIRE-AND-FORGET an async Celery dispatch. A notification
failure — broker down, ACI down, channel unconfigured — must NEVER block or
fail the underlying business operation; that guarantee lives here, in one
place, not at each call site.

The notification itself executes through the governance registry's
`notify_slack_internal` action (fixed admin-configured destination), so the
approval decision still happens inside DataOne — the message only links
back to DataOne's own UI.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.notification_setting import NotificationSetting
from app.models.in_app_notification import InAppNotification, InAppNotificationRead

logger = logging.getLogger(__name__)


def create_in_app_notification(db: Session, *, event_key: str, title: str,
                               body: str = "", link: Optional[str] = None) -> None:
    """Stage a feed entry in the caller's transaction; ACI opt-in is irrelevant."""
    try:
        db.add(InAppNotification(
            event_key=event_key, title=title[:200], body=body, link=link,
        ))
        db.flush()
    except Exception as exc:
        logger.warning("[notifications] in-app persistence failed for %s: %s", event_key, exc)


def list_in_app_notifications(db: Session, *, user_id: int, limit: int = 30) -> list[dict]:
    rows = (
        db.query(InAppNotification, InAppNotificationRead.id)
        .outerjoin(
            InAppNotificationRead,
            (InAppNotificationRead.notification_id == InAppNotification.id)
            & (InAppNotificationRead.user_id == user_id),
        )
        .order_by(InAppNotification.created_at.desc(), InAppNotification.id.desc())
        .limit(limit)
        .all()
    )
    return [{"notification": row, "read": receipt_id is not None} for row, receipt_id in rows]


def unread_count(db: Session, *, user_id: int) -> int:
    return (
        db.query(InAppNotification)
        .outerjoin(
            InAppNotificationRead,
            (InAppNotificationRead.notification_id == InAppNotification.id)
            & (InAppNotificationRead.user_id == user_id),
        )
        .filter(InAppNotificationRead.id.is_(None))
        .count()
    )


def mark_read(db: Session, notification_id: int, *, user_id: int) -> None:
    exists = db.query(InAppNotification.id).filter(InAppNotification.id == notification_id).first()
    if not exists:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Notification not found")
    receipt = db.query(InAppNotificationRead).filter(
        InAppNotificationRead.notification_id == notification_id,
        InAppNotificationRead.user_id == user_id,
    ).first()
    if not receipt:
        db.add(InAppNotificationRead(notification_id=notification_id, user_id=user_id))
        db.commit()


def is_notify_enabled(db: Session, event_key: str) -> bool:
    row = (
        db.query(NotificationSetting)
        .filter(NotificationSetting.event_key == event_key)
        .first()
    )
    return bool(row and row.enabled)


def set_notify_enabled(db: Session, event_key: str, enabled: bool, *,
                       actor: str) -> NotificationSetting:
    row = (
        db.query(NotificationSetting)
        .filter(NotificationSetting.event_key == event_key)
        .first()
    )
    if row:
        row.enabled = enabled
        row.updated_by = actor
    else:
        row = NotificationSetting(event_key=event_key, enabled=enabled,
                                  updated_by=actor)
        db.add(row)
    db.flush()
    return row


def list_notification_settings(db: Session) -> list[NotificationSetting]:
    return (
        db.query(NotificationSetting)
        .order_by(NotificationSetting.event_key)
        .all()
    )


def dispatch_notify_out(db: Session, *, event_key: str, title: str,
                        body: str = "", link: Optional[str] = None) -> bool:
    """Fire-and-forget notify-out. Returns True if a dispatch was enqueued.

    Never raises: an opt-out, a missing broker, or any other failure logs a
    warning and returns False — the caller's business operation proceeds
    untouched either way.
    """
    create_in_app_notification(db, event_key=event_key, title=title, body=body, link=link)

    try:
        if not is_notify_enabled(db, event_key):
            return False
    except Exception as exc:  # settings table unreadable — fail closed, quietly
        logger.warning("[aci] notify-out settings check failed for %s: %s", event_key, exc)
        return False

    try:
        from app.tasks.aci_tasks import notify_out_task
        notify_out_task.delay(event_key=event_key, title=title, body=body, link=link)
        logger.info("[aci] notify-out dispatched event_key=%s", event_key)
        return True
    except Exception as exc:
        logger.warning("[aci] notify-out dispatch failed for %s (business "
                       "operation unaffected): %s", event_key, exc)
        return False
