"""Pydantic schemas for the Agentic DBA Copilot API (agentic_dba_tasks #3/#6)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PlanCreateRequest(BaseModel):
    connection_id: int
    question: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    # Optional distinct target connection: required for draft-mapping
    # auto-creation (Schema Mapper needs source != target); omitted = the
    # proposed tables are created in the source connection itself.
    target_connection_id: Optional[int] = None


class PlanCreateResponse(BaseModel):
    plan_id: int
    status: str


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: Optional[str]
    question: str
    source_connection_id: int
    target_connection_id: Optional[int]
    status: str
    domain_template: Optional[str]
    dialect: Optional[str]
    proposed_tables: Optional[List[Dict[str, Any]]]
    dq_rules: Optional[List[Dict[str, Any]]]
    transformations: Optional[List[Dict[str, Any]]]
    generated_ddl: Optional[List[Dict[str, Any]]]
    confidence_notes: Optional[List[str]]
    apply_results: Optional[List[Dict[str, Any]]]
    created_mapping_id: Optional[int]
    error: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    decided_by: Optional[str]
    decided_at: Optional[datetime]


class PlanListResponse(BaseModel):
    plans: List[PlanResponse]
