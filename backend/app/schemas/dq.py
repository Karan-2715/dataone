"""Pydantic schemas for the Data Quality Observatory (Enterprise v2, E06)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ColumnScorecard(BaseModel):
    column: str
    completeness: float | None
    uniqueness: float | None
    consistency: float | None
    accuracy: float | None
    freshness: float | None
    overall: float | None
    profiled: bool


class TableScorecard(BaseModel):
    table: str
    column_count: int
    profiled_column_count: int
    overall: float | None
    columns: list[ColumnScorecard]
    last_scanned_at: datetime | None


class ConnectionScorecard(BaseModel):
    connection_id: int
    connection_name: str
    overall: float | None
    table_count: int
    column_count: int
    profiled_column_count: int
    tables: list[TableScorecard]


class DQSummary(BaseModel):
    overall: float | None
    connection_count: int
    profiled_column_count: int
    column_count: int
