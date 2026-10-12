"""Pydantic schemas for the Dashboard aggregation API (Task #1)."""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class KPITile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    value: int
    subtitle: str | None = None
    trend: Literal["up", "down", "neutral"] | None = None
    trend_label: str | None = None
    icon: str | None = None
    link_url: str
    module: str
    status: Literal["loaded", "error", "unavailable"]
    error_message: str | None = None


class FeedItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    actor: str
    module: str
    summary: str
    status: str
    created_at: datetime
    link_url: str | None = None


class DashboardSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kpis: List[KPITile]
    feed: List[FeedItem]
    range: str
    generated_at: datetime
    available_views: List[Literal["combined", "executive", "operational"]] = Field(
        default_factory=lambda: ["combined", "executive", "operational"]
    )
