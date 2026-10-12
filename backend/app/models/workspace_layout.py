"""Per-user dockable workspace layout persistence (Enterprise v2, E11).

One row per (user, workspace_key) holding a small JSON blob describing
panel states (normal/collapsed/maximized) and widths. This is a UI
convenience — losing it never loses data, only a saved arrangement — so
it stays a thin key/value store rather than a modeled schema.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class WorkspaceLayout(Base):
    __tablename__ = "workspace_layouts"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_key", name="uq_workspace_layout_user_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_key = Column(String, nullable=False, index=True)
    layout = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)
