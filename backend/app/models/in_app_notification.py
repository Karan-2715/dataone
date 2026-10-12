"""Persisted in-app feed generated from the existing notification triggers."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class InAppNotification(Base):
    __tablename__ = "in_app_notifications"

    id = Column(Integer, primary_key=True, index=True)
    event_key = Column(String, nullable=False, index=True)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False, default="")
    link = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class InAppNotificationRead(Base):
    __tablename__ = "in_app_notification_reads"
    __table_args__ = (UniqueConstraint("notification_id", "user_id", name="uq_notification_read_user"),)

    id = Column(Integer, primary_key=True)
    notification_id = Column(Integer, ForeignKey("in_app_notifications.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    read_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
