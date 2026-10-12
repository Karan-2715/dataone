"""Pydantic schemas for the semantic / metrics layer API (DP-SEM-001).

Mirrors the convention in app/schemas/{mapping,pipeline}.py.

The `definition` body is JSON; its shape is validated by the
semantic_definition service (Task #4) at create/update time. For now
the schema accepts any JSON object — Task #4 will tighten it to the
design-review-approved shape.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Request bodies ────────────────────────────────────────────────


class SemanticEntityCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    owner: Optional[str] = None


class SemanticEntityUpdate(BaseModel):
    description: Optional[str] = None
    owner: Optional[str] = None


class SemanticDimensionCreate(BaseModel):
    entity_id: int = Field(..., ge=1)
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    semantic_type: str = Field(default="categorical", max_length=64)
    # Task #4: optional physical mapping at create time. Validation of
    # the catalog column is done in the service layer.
    catalog_column_id: Optional[int] = Field(default=None, ge=1)


class SemanticMeasureCreate(BaseModel):
    entity_id: int = Field(..., ge=1)
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    default_aggregation: str = Field(default="sum", max_length=64)
    catalog_column_id: Optional[int] = Field(default=None, ge=1)


class SemanticMetricCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    definition: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None
    certified: bool = False
    owner: Optional[str] = None


class SemanticMetricUpdate(BaseModel):
    definition: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    certified: Optional[bool] = None
    owner: Optional[str] = None


class SemanticLineageCreate(BaseModel):
    metric_id: int = Field(..., ge=1)
    catalog_column_id: int = Field(..., ge=1)
    role: str = Field(default="measure", max_length=64)


# ── Response bodies ────────────────────────────────────────────────


class SemanticEntityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    owner: Optional[str] = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class SemanticDimensionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    name: str
    description: Optional[str] = None
    semantic_type: str
    catalog_column_id: Optional[int] = None


class SemanticMeasureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    name: str
    description: Optional[str] = None
    default_aggregation: str
    catalog_column_id: Optional[int] = None


class SemanticMetricRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    version_number: int
    status: str
    definition: Dict[str, Any]
    description: Optional[str] = None
    certified: bool
    owner: Optional[str] = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None
    published_by: Optional[str] = None


class SemanticMetricReadWithRelations(SemanticMetricRead):
    """Metric with its dimensions/measure lineage surfaced for the catalog."""

    lineage: List["SemanticLineageRead"] = Field(default_factory=list)


class SemanticLineageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    metric_id: int
    catalog_column_id: Optional[int] = None
    role: str


class ResolutionRequest(BaseModel):
    """POST /semantic/resolve request body."""

    metric_id: int = Field(..., ge=1)
    dimensions: List[str] = Field(default_factory=list)
    filters: Dict[str, Any] = Field(default_factory=dict)


class ResolutionResponse(BaseModel):
    """POST /semantic/resolve response shape (Task #6 fills in details)."""

    metric_id: int
    metric_name: str
    metric_version: int
    sql: str
    placeholders: List[Any] = Field(default_factory=list)
    lineage: List[SemanticLineageRead] = Field(default_factory=list)


SemanticMetricReadWithRelations.model_rebuild()
