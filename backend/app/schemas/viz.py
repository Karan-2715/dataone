"""Pydantic schemas for the Visualize charting API (Visualize Task #1)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

AGGREGATIONS = {"sum", "avg", "count", "min", "max"}
OPERATORS = {"eq", "neq", "gt", "lt", "gte", "lte", "contains", "between"}
CHART_TYPES = {"table", "bar", "line", "area", "pie", "scatter", "kpi"}


class MeasureSpec(BaseModel):
    field: str = Field(..., min_length=1, max_length=200)
    aggregation: str = Field(..., min_length=1)
    label: Optional[str] = None

    @field_validator("aggregation")
    @classmethod
    def _valid_agg(cls, v: str) -> str:
        if v not in AGGREGATIONS:
            raise ValueError(f"aggregation must be one of {sorted(AGGREGATIONS)}")
        return v


class FilterSpec(BaseModel):
    field: str = Field(..., min_length=1, max_length=200)
    operator: str = Field(..., min_length=1)
    value: Any = None

    @field_validator("operator")
    @classmethod
    def _valid_op(cls, v: str) -> str:
        if v not in OPERATORS:
            raise ValueError(f"operator must be one of {sorted(OPERATORS)}")
        return v


class VizQueryRequest(BaseModel):
    connection_id: int = Field(..., ge=1)
    table_name: str = Field(..., min_length=1, max_length=200)
    dimensions: List[str] = Field(default_factory=list)
    measures: List[MeasureSpec] = Field(default_factory=list)
    filters: List[FilterSpec] = Field(default_factory=list)


class VizQueryResponse(BaseModel):
    columns: List[str]
    rows: List[List[Any]]
    row_count: int
    truncated: bool = False


class VizViewCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    connection_id: int = Field(..., ge=1)
    table_name: str = Field(..., min_length=1, max_length=200)
    chart_type: str = Field(default="table")
    dimensions: List[str] = Field(default_factory=list)
    measures: List[MeasureSpec] = Field(default_factory=list)
    filters: List[FilterSpec] = Field(default_factory=list)

    @field_validator("chart_type")
    @classmethod
    def _valid_chart_type(cls, v: str) -> str:
        if v not in CHART_TYPES:
            raise ValueError(f"chart_type must be one of {sorted(CHART_TYPES)}")
        return v


class VizViewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    connection_id: int
    table_name: str
    chart_type: str
    dimensions: List[str]
    measures: List[dict]
    filters: List[dict]
    created_by: str
    created_at: datetime
    updated_at: datetime


class VizViewListResponse(BaseModel):
    items: List[VizViewRead]
    total: int
