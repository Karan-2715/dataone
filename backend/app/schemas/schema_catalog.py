"""Pydantic schemas for the Schema Intel catalog API (Tasks #1-#4, #7)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Response bodies ──────────────────────────────────────────────────


class CatalogForeignKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    references_table: str
    references_column: str


class ColumnProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    null_count: int
    null_rate: float
    distinct_count: Optional[int]
    min_value: Optional[str]
    max_value: Optional[str]
    sample_size_used: int
    profiled_at: datetime
    # Enrichment (agentic_dba_tasks #2) — nullable until a re-profile runs.
    row_count: Optional[int] = None
    uniqueness_ratio: Optional[float] = None
    duplicate_count: Optional[int] = None
    fk_candidates: Optional[List[Dict[str, Any]]] = None


class ColumnClassificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    level: str
    confidence: float
    method: str
    overridden_by: Optional[str] = None
    overridden_at: Optional[datetime] = None
    classified_at: datetime


class CatalogColumnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    column_name: str
    data_type: str | None
    nullable: bool
    is_primary_key: bool
    ordinal_position: int
    foreign_keys: List[CatalogForeignKeyResponse] = []
    profile: Optional[ColumnProfileResponse] = None
    classification: Optional[ColumnClassificationResponse] = None


class CatalogTableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connection_id: int
    table_name: str
    last_scanned_at: datetime
    columns: List[CatalogColumnResponse] = []


class CatalogTableListResponse(BaseModel):
    connection_id: int
    tables: List[CatalogTableResponse]


class ScanResult(BaseModel):
    connection_id: int
    tables_scanned: int
    columns_scanned: int
    scanned_at: datetime


class ProfileEnqueueResult(BaseModel):
    status: str
    task_id: Optional[str] = None
    message: str


class GlobalSearchResult(BaseModel):
    kind: str
    connection_id: int
    connection_name: str
    table_id: Optional[int] = None
    table_name: Optional[str] = None
    column_id: Optional[int] = None
    column_name: Optional[str] = None
    data_type: Optional[str] = None


class GlobalSearchResponse(BaseModel):
    query: str
    page: int
    page_size: int
    total: int
    results: List[GlobalSearchResult]


# ── Request bodies ────────────────────────────────────────────────────


class ClassificationOverrideRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=50)
    level: str = Field(..., min_length=1, max_length=20)
