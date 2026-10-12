"""Pydantic schemas for Impact Analysis (Enterprise v2, E07)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ImpactResult(BaseModel):
    connection_id: int
    connection_name: str
    table: str
    column: str | None
    upstream: list[dict[str, Any]]
    downstream: list[dict[str, Any]]
    affected_pipelines: list[dict[str, Any]]
    affected_metrics: list[dict[str, Any]]
    affected_reports: list[dict[str, Any]]
    affected_ml_models: list[dict[str, Any]]
    is_pii: bool
    risk_score: int
    risk_score_explanation: str
