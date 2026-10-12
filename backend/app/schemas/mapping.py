"""Pydantic schemas for the mapping workspace API (Schema Mapper)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Request bodies ────────────────────────────────────────────────


class MappingCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    source_id: int = Field(..., ge=1)
    target_id: int = Field(..., ge=1)


class MappingUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)


class SourceRef(BaseModel):
    table: str = Field(..., min_length=1)
    column: str = Field(..., min_length=1)
    type: Optional[str] = None
    nullable: Optional[bool] = None


class TargetRef(BaseModel):
    table: str = Field(..., min_length=1)
    column: str = Field(..., min_length=1)
    type: Optional[str] = None
    nullable: Optional[bool] = None
    primary_key: Optional[bool] = None


class EdgeCreate(BaseModel):
    target: TargetRef
    sources: List[SourceRef] = Field(..., min_length=1)
    transformation: Dict[str, Any] = Field(default_factory=dict)
    origin: str = Field(default="manual")

    @field_validator("origin")
    @classmethod
    def _origin(cls, v: str) -> str:
        if v not in {"manual", "ai_accepted", "english_parsed"}:
            raise ValueError("origin must be manual | ai_accepted | english_parsed")
        return v


class EdgeTransformationUpdate(BaseModel):
    transformation: Dict[str, Any]


class SuggestionAcceptRequest(BaseModel):
    transformation: Dict[str, Any] = Field(default_factory=dict)


# ── Response bodies ───────────────────────────────────────────────


class EdgeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mapping_id: int
    target: TargetRef
    sources: List[SourceRef]
    transformation: Dict[str, Any]
    origin: str
    ai_confidence: Optional[float] = None
    audit: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class MappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_id: Optional[int] = None
    target_id: Optional[int] = None
    status: str
    review_stage: str = "draft"
    current_version_id: Optional[int] = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    latest_run_id: Optional[int] = None
    latest_run_state: Optional[str] = None
    edges: List[EdgeResponse] = Field(default_factory=list)


class SuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mapping_id: int
    target_table: str
    target_column: str
    target_type: Optional[str] = None
    source_table: str
    source_column: str
    source_type: Optional[str] = None
    confidence: float
    reason: Optional[str] = None
    components: Optional[dict[str, float]] = None
    suggested_transformation: Optional[Dict[str, Any]] = None
    transformation_note: Optional[str] = None
    status: str
    created_at: datetime
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None


class ValidationIssue(BaseModel):
    edge_id: Optional[int] = None
    suggestion_id: Optional[int] = None
    verdict: str  # ok | lossy_warning | blocking
    message: str


class ValidationResponse(BaseModel):
    mapping_id: int
    ok_count: int
    warning_count: int
    blocking_count: int
    issues: List[ValidationIssue]


class PublishResponse(BaseModel):
    mapping_id: int
    version_number: int
    version_id: int
    status: str
    published_at: datetime
    published_by: str


class MappingListResponse(BaseModel):
    """Review §11.8: paginated list shape so callers can page through
    ≥10,000 mappings per tenant instead of receiving an unbounded array."""

    items: List[MappingResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class SuggestionListResponse(BaseModel):
    """Paginated shape for GET /mappings/{id}/suggestions (review §11.8)."""

    items: List[SuggestionResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class PreviewRow(BaseModel):
    """One sample row for the E10-7 transformation preview."""

    source_values: Dict[str, Any]
    output: Any = None


class ReviewTransitionRequest(BaseModel):
    """E13 — Mapping Review & Approval Workflow."""

    to_stage: str
    # E16-3 — an optional note becomes a steward comment tied to this
    # exact transition ("rejected because...").
    note: Optional[str] = None


class ReviewQueueResponse(BaseModel):
    items: List[MappingResponse]
    total: int


class ReportResponse(BaseModel):
    """E15 — a generated documentation or migration-report artifact."""

    content: str
    filename: str


class AnnotationCreate(BaseModel):
    """E16-1/2 — a comment pinned to a mapping, or to one of its edges."""

    body: str = Field(..., min_length=1, max_length=4000)
    edge_id: Optional[int] = None
    parent_id: Optional[int] = None


class AnnotationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mapping_id: int
    edge_id: Optional[int]
    parent_id: Optional[int]
    kind: str
    review_stage: Optional[str]
    author: str
    body: str
    created_at: datetime


class FeedbackSummaryItem(BaseModel):
    """E14-5 — one (source_column, target_column) pair with corrections."""

    source_column: str
    target_column: str
    accepted_count: int
    rejected_count: int


class FeedbackSummaryResponse(BaseModel):
    """E14-5 — acceptance-rate + most-corrected pairs, computed live from
    AISuggestion outcomes (heuristic memory, not a trained model)."""

    total_decided: int
    total_accepted: int
    total_rejected: int
    acceptance_rate: float
    most_corrected: List[FeedbackSummaryItem]


class VersionSummaryResponse(BaseModel):
    """E13-5/6 — one immutable `mapping_versions` row, summarized."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    version_number: int
    status: str
    published_at: Optional[datetime] = None
    published_by: Optional[str] = None
    edge_count: int
    is_current: bool


class VersionListResponse(BaseModel):
    items: List[VersionSummaryResponse]


class EdgeSnapshot(BaseModel):
    """One field mapping as it was frozen into a version's edges_snapshot."""

    target: TargetRef
    sources: List[SourceRef]
    transformation: Dict[str, Any] = Field(default_factory=dict)
    origin: str = "manual"
    ai_confidence: Optional[float] = None


class EdgeDiffItem(BaseModel):
    target_table: str
    target_column: str
    before: EdgeSnapshot
    after: EdgeSnapshot


class VersionDiffResponse(BaseModel):
    """E13-5 — field-level diff between two published versions."""

    mapping_id: int
    from_version: VersionSummaryResponse
    to_version: VersionSummaryResponse
    added: List[EdgeSnapshot]
    removed: List[EdgeSnapshot]
    changed: List[EdgeDiffItem]
    unchanged_count: int


class TransformationPreviewResponse(BaseModel):
    """E10-7 (Schema Mapping Workbench) — 'available' is False (with a
    human-readable 'reason') for transformation kinds/edge shapes that
    can't be safely previewed on sample data (e.g. lookup joins), never
    a fabricated preview."""

    available: bool
    reason: Optional[str] = None
    rows: List[PreviewRow]


class MappingRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mapping_id: int
    databricks_run_id: Optional[str] = None
    state: str
    rows_read: Optional[int] = None
    rows_written: Optional[int] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime


class RunStatusUpdate(BaseModel):
    state: str
    rows_read: Optional[int] = None
    rows_written: Optional[int] = None
    error: Optional[str] = None
