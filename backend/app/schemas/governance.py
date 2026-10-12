"""Pydantic schemas for the Governance Intelligence Center (Enterprise v2, E08)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GovernanceMetadataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connection_id: int
    table_name: str | None
    column_name: str | None
    owner: str | None
    steward: str | None
    classification: str | None
    retention_policy: str | None
    compliance_status: str
    updated_by: str
    updated_at: datetime


class GovernanceMetadataUpsert(BaseModel):
    table_name: str | None = None
    column_name: str | None = None
    owner: str | None = None
    steward: str | None = None
    classification: str | None = None
    retention_policy: str | None = None
    compliance_status: str | None = None


class GovernanceScore(BaseModel):
    score: int
    table_count: int
    owner_coverage: int
    classification_coverage: int
    retention_coverage: int
