"""Dockable workspace layout persistence (Enterprise v2, E11-8).

A thin per-user key/value store for panel arrangement (state + width per
named panel) so a docked workspace survives a reload. Deliberately not a
generic preferences store: only the workspace keys a real screen actually
uses are accepted, and the blob size is bounded — this is a UI nicety,
not a place for arbitrary client-supplied data to accumulate.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.models.workspace_layout import WorkspaceLayout

logger = logging.getLogger(__name__)

# Every dockable workspace this feature has actually been wired into.
# Add a key here only when a real screen consumes it.
ALLOWED_WORKSPACE_KEYS = {"schema-mapper"}

MAX_LAYOUT_BYTES = 20_000


class WorkspaceLayoutService:
    @staticmethod
    def get(db: Session, user_id: int, workspace_key: str) -> dict:
        if workspace_key not in ALLOWED_WORKSPACE_KEYS:
            raise ValueError(f"unknown workspace_key '{workspace_key}'")
        row = (
            db.query(WorkspaceLayout)
            .filter(
                WorkspaceLayout.user_id == user_id,
                WorkspaceLayout.workspace_key == workspace_key,
            )
            .first()
        )
        return row.layout if row is not None else {}

    @staticmethod
    def upsert(db: Session, user_id: int, workspace_key: str, layout: dict) -> dict:
        if workspace_key not in ALLOWED_WORKSPACE_KEYS:
            raise ValueError(f"unknown workspace_key '{workspace_key}'")
        serialized = json.dumps(layout)
        if len(serialized.encode("utf-8")) > MAX_LAYOUT_BYTES:
            raise ValueError("layout payload too large")

        row = (
            db.query(WorkspaceLayout)
            .filter(
                WorkspaceLayout.user_id == user_id,
                WorkspaceLayout.workspace_key == workspace_key,
            )
            .first()
        )
        if row is None:
            row = WorkspaceLayout(user_id=user_id, workspace_key=workspace_key, layout=layout)
            db.add(row)
        else:
            row.layout = layout
        db.commit()
        db.refresh(row)
        logger.info("[workspace_layout] stage=saved user_id=%s workspace_key=%s", user_id, workspace_key)
        return row.layout
