from datetime import datetime
from typing import Any, Dict, Optional, List
from pydantic import BaseModel, Field

from app.core.config import settings


class AuditEventCreate(BaseModel):
    """Canonical audit event schema (AUDIT-T1).

    All modules emit events conforming to this schema.
    """
    event_type: str = Field(..., description="e.g. connector.created, query.executed")
    actor: str = Field(default="system", description="User identity")
    module: Optional[str] = Field(default=None, description="Source module: connectors, query_studio, askdata, etc.")
    target_type: Optional[str] = Field(default=None, description="connection, query, pipeline, mapping, etc.")
    target_id: Optional[str] = Field(default=None, description="ID of the target entity")
    target_name: Optional[str] = Field(default=None, description="Human-readable target name")
    before: Optional[Dict[str, Any]] = Field(default=None, description="State before change")
    after: Optional[Dict[str, Any]] = Field(default=None, description="State after change")
    correlation_id: Optional[str] = Field(default=None, description="UUID linking events across modules")
    outcome: str = Field(default="success", description="success, failure, warning")
    summary: Optional[str] = Field(default=None, description="Human-readable summary")
    duration_ms: Optional[int] = Field(default=None, description="Operation duration in ms")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional structured data")

    # Legacy fields (backward compatibility)
    connection_id: Optional[int] = Field(default=None)
    connection_name: Optional[str] = Field(default=None)
    payload: Optional[Dict[str, Any]] = Field(default=None)
    status: str = Field(default="success")


class AuditEventResponse(BaseModel):
    """Response schema for a single audit event."""
    id: int
    event_type: str
    actor: str
    module: Optional[str]
    target_type: Optional[str]
    target_id: Optional[str]
    target_name: Optional[str]
    before_summary: Optional[Dict[str, Any]]
    after_summary: Optional[Dict[str, Any]]
    correlation_id: Optional[str]
    outcome: str
    summary: Optional[str]
    duration_ms: Optional[int]
    metadata: Optional[Dict[str, Any]] = Field(default=None, validation_alias="event_metadata")
    connection_id: Optional[int]
    connection_name: Optional[str]
    payload: Optional[Dict[str, Any]]
    status: str
    event_hash: Optional[str]
    sequence: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditEventBatchRequest(BaseModel):
    """Batch ingestion of audit events (AUDIT-T2)."""
    events: List[AuditEventCreate] = Field(
        ..., max_length=settings.AUDIT_INGEST_BATCH_MAX,
        description=f"Batch of events (max {settings.AUDIT_INGEST_BATCH_MAX})",
    )


class AuditEventBatchResponse(BaseModel):
    """Response for batch ingestion."""
    accepted: int
    rejected: int
    errors: List[Dict[str, Any]] = Field(default_factory=list)


class AuditSearchFilters(BaseModel):
    """Search/filter parameters for audit queries (AUDIT-T4)."""
    actor: Optional[str] = None
    module: Optional[str] = None
    event_type: Optional[str] = None
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    correlation_id: Optional[str] = None
    outcome: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    search: Optional[str] = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    sort_by: str = "created_at"
    sort_order: str = "desc"


class AuditFacets(BaseModel):
    """Faceted search aggregates (AUDIT-T4)."""
    modules: Dict[str, int] = Field(default_factory=dict)
    event_types: Dict[str, int] = Field(default_factory=dict)
    outcomes: Dict[str, int] = Field(default_factory=dict)
    actors: Dict[str, int] = Field(default_factory=dict)
    date_range: Optional[Dict[str, datetime]] = None


class AuditSearchResponse(BaseModel):
    """Paginated search results with facets."""
    events: List[AuditEventResponse]
    total: int
    page: int
    page_size: int
    has_more: bool
    facets: Optional[AuditFacets] = None


class IntegrityVerificationResult(BaseModel):
    """Result of hash chain integrity verification (AUDIT-T3)."""
    valid: bool
    total_events: int
    verified_events: int
    chain_broken_at: Optional[int] = None
    tampered_events: List[int] = Field(default_factory=list)


class RetentionStatus(BaseModel):
    """Retention policy status (AUDIT-T7)."""
    retention_days: int
    total_events: int
    events_in_retention_window: int
    events_expired: int
    next_cleanup_at: Optional[str] = None