"""Authenticated in-app notification feed."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services import notification_service

router = APIRouter()


@router.get("")
def list_notifications(
    limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = notification_service.list_in_app_notifications(db, user_id=user.id, limit=limit)
    return {"notifications": [{
        "id": item["notification"].id,
        "event_key": item["notification"].event_key,
        "title": item["notification"].title,
        "body": item["notification"].body,
        "link": item["notification"].link,
        "created_at": item["notification"].created_at,
        "read": item["read"],
    } for item in rows]}


@router.get("/unread-count")
def get_unread_count(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"unread": notification_service.unread_count(db, user_id=user.id)}


@router.patch("/{notification_id}/read")
def mark_notification_read(
    notification_id: int, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    notification_service.mark_read(db, notification_id, user_id=user.id)
    return {"id": notification_id, "read": True}
