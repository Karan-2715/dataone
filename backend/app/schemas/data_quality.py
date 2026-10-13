"""Pydantic schemas for the Databricks data-quality ingest + dashboard read APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

Score = Optional[float]


def _score() -> Score:
    return Field(default=None, ge=0, le=100)


class DQIssue(BaseModel):
    column: Optional[str] = Field(default=None, max_length=255)
    check: str = Field(max_length=64)
    detail: str = Field(max_length=500)


class DQTableIn(BaseModel):
    table_name: str = Field(min_length=1, max_length=512)
    row_count: Optional[int] = Field(default=None, ge=0)
    column_count: Optional[int] = Field(default=None, ge=0)
    completeness: Score = _score()
    uniqueness: Score = _score()
    validity: Score = _score()
    freshness: Score = _score()
    last_modified: Optional[datetime] = None
    issues: List[DQIssue] = Field(default_factory=list, max_length=20)


class DQRunIn(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)
    source: str = Field(default="databricks", max_length=64)
    catalog: Optional[str] = Field(default=None, max_length=255)
    schema_name: Optional[str] = Field(default=None, max_length=255)
    completed_at: Optional[datetime] = None
    tables: List[DQTableIn] = Field(min_length=1, max_length=500)


class DQCatalogRunIn(BaseModel):
    catalog: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(min_length=1, max_length=255)
    table: str = Field(min_length=1, max_length=255)


class DQIngestResult(BaseModel):
    run_id: str
    table_count: int
    overall_score: Score
    replaced: bool


class DQTableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_name: str
    row_count: Optional[int]
    column_count: Optional[int]
    completeness: Score
    uniqueness: Score
    validity: Score
    freshness: Score
    overall_score: Score
    last_modified: Optional[datetime]
    issues: List[DQIssue] = Field(default_factory=list)


class DQRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    source: str
    catalog: Optional[str]
    schema_name: Optional[str]
    table_count: int
    total_rows: Optional[int]
    overall_score: Score
    completeness: Score
    uniqueness: Score
    validity: Score
    freshness: Score
    completed_at: datetime


class DQTrendPoint(BaseModel):
    completed_at: datetime
    overall_score: Score


class DQSummary(BaseModel):
    """``latest`` is null until the first Databricks run has reported in."""
    latest: Optional[DQRunOut]
    tables: List[DQTableOut]
    trend: List[DQTrendPoint]
    ingest_configured: bool
    scope: str = "all"
    scope_counts: Dict[str, int] = Field(default_factory=dict)  # datasets per scope: all / files / tables
