"""Pydantic schemas for dockable workspace layout persistence (Enterprise v2, E11)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class WorkspaceLayoutResponse(BaseModel):
    workspace_key: str
    layout: dict[str, Any]


class WorkspaceLayoutUpdate(BaseModel):
    layout: dict[str, Any]
