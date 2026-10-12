from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class QueryExecuteRequest(BaseModel):
    """Execute raw SQL against a connection (QS-T1/T2/T3)."""
    connection_id: int
    sql: str = Field(..., min_length=1)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=1000)
    confirm: bool = Field(
        default=False,
        description="Must be true to actually run a write/DDL statement — "
                     "without it, the endpoint classifies the statement and "
                     "returns requires_confirmation=True without executing.",
    )


class QueryExecuteResponse(BaseModel):
    statement_type: str
    tables_referenced: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    executed: bool = False
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    affected_rows: Optional[int] = None
    page: int = 1
    page_size: int = 100
    has_more: bool = False
    truncated: bool = False
    duration_ms: Optional[int] = None
    error: Optional[str] = None


class SavedQueryCreate(BaseModel):
    connection_id: int
    name: str = Field(..., min_length=1, max_length=200)
    sql_text: str = Field(..., min_length=1)


class SavedQueryResponse(BaseModel):
    id: int
    connection_id: int
    name: str
    sql_text: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class QueryHistoryEntry(BaseModel):
    """One past execution, sourced from the audit log (module=query_studio)."""
    id: int
    actor: str
    sql: Optional[str] = None
    connection_id: Optional[str] = None
    statement_type: Optional[str] = None
    outcome: str
    row_count: Optional[int] = None
    duration_ms: Optional[int] = None
    created_at: datetime


class QueryHistoryResponse(BaseModel):
    history: List[QueryHistoryEntry]
    total: int
    page: int
    page_size: int
