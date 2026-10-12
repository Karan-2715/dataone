"""Pydantic schemas for the pipeline workspace API (Pipelines, DP-PIPE-001).

Mirrors the convention in app/schemas/mapping.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Request bodies ────────────────────────────────────────────────


class PipelineCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    source_connection_id: int = Field(..., ge=1)
    target_connection_id: int = Field(..., ge=1)
    # mapping_id is required at create time; mapping_version_id is pinned
    # from the current published version at create time (Task #1 design).
    mapping_id: int = Field(..., ge=1)
    # "auto" triggers a run immediately after creation (same as clicking
    # Run Now); "manual" leaves the pipeline idle until the user runs it.
    execution_mode: Literal["auto", "manual"] = "manual"
    # None = infer per table-group from natural keys (today's behavior);
    # an explicit value overrides that inference for every table-group.
    load_strategy: Optional[Literal["upsert", "full_refresh", "append"]] = None


class PipelineUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    enabled: Optional[bool] = None
    # Updates to source/target connection or pinned mapping are intentionally
    # NOT supported in this update path -- changing the mapping would
    # invalidate the pinned mapping_version_id and the drift baseline.
    # The user creates a new pipeline instead.


class ScheduleUpsert(BaseModel):
    cron_expression: str = Field(..., min_length=1, max_length=200)
    enabled: bool = True
    timezone: str = Field(default="UTC", min_length=1, max_length=64)


class RetryPolicyUpsert(BaseModel):
    max_attempts: int = Field(3, ge=1, le=10)
    backoff_seconds: int = Field(60, ge=1, le=86400)
    # Optional list of substrings matched against error_message; only
    # matching errors trigger a retry. Empty/null = executor decides.
    retryable_error_patterns: Optional[List[str]] = None


# ── Response bodies ────────────────────────────────────────────────


class PipelineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_connection_id: int
    target_connection_id: int
    mapping_id: int
    mapping_version_id: int
    enabled: bool
    execution_mode: str
    load_strategy: Optional[str] = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class PipelineReadWithRelations(PipelineRead):
    """Pipeline with its schedule and retry_policy embedded for the UI."""

    schedule: Optional["ScheduleRead"] = None
    retry_policy: Optional["RetryPolicyRead"] = None


class PipelineCreateResponse(PipelineReadWithRelations):
    """PipelineReadWithRelations plus the run auto-triggered on create, if any."""

    run_id: Optional[int] = None
    task_id: Optional[str] = None


class ScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pipeline_id: int
    cron_expression: str
    enabled: bool
    timezone: str
    next_run_at: Optional[datetime] = None


class RetryPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pipeline_id: int
    max_attempts: int
    backoff_seconds: int
    retryable_error_patterns: Optional[List[str]] = None


class PipelineRunStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    step: str  # extract | transform | load
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    rows_processed: int
    error_message: Optional[str] = None


class PipelineRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pipeline_id: int
    status: str
    trigger: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    rows_processed: int
    error_message: Optional[str] = None
    retry_count: int
    parent_run_id: Optional[int] = None


class PipelineRunReadWithSteps(PipelineRunRead):
    """Run with its step-level detail (FR6)."""

    steps: List[PipelineRunStepRead] = Field(default_factory=list)


class DriftValidationRead(BaseModel):
    """Result of Task #2's drift check."""

    pipeline_id: int
    has_drift: bool
    baseline_hash: Optional[str] = None
    current_hash: Optional[str] = None
    changed_tables: List[str] = Field(default_factory=list)
    message: str


PipelineReadWithRelations.model_rebuild()
PipelineCreateResponse.model_rebuild()
