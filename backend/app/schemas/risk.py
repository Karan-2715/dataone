"""Pydantic schemas for the Risk & Compliance Center (Enterprise v2, E05).

Findings are computed live from existing signal sources (schema diff,
drift events, PII classification, mapping validation, pipeline drift
blocks) — this module never re-detects anything; it normalizes what
other services already produce. See RiskAggregationService.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

RiskCategory = Literal[
    "missing_target_table",
    "schema_drift",
    "pii_exposure",
    "unsupported_transformation",
    "broken_dependency",
]
RiskSeverity = Literal["critical", "high", "medium", "low"]


class RiskFinding(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category: RiskCategory
    severity: RiskSeverity
    title: str
    description: str
    root_cause: str
    impact: str
    remediation: str
    effort: Literal["S", "M", "L"]
    connection_id: int | None = None
    connection_name: str | None = None
    mapping_id: int | None = None
    table: str | None = None
    column: str | None = None
    detected_at: datetime | None = None
    evidence: dict[str, Any] = {}


class RiskFacets(BaseModel):
    by_category: dict[str, int]
    by_severity: dict[str, int]


class RiskRegister(BaseModel):
    total: int
    findings: list[RiskFinding]
    facets: RiskFacets
    partial: bool = False
    partial_reason: str | None = None
