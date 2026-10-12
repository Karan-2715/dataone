"""Pydantic schemas for Schema Comparison Mode (Enterprise v2, E12)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class TableComparison(BaseModel):
    table: str
    status: Literal["matched", "source_only", "target_only"]
    added_columns: list[str]
    missing_columns: list[str]
    changed_types: list[dict[str, Any]]
    changed_constraints: list[dict[str, Any]]
    column_count: int


class ComparisonSummary(BaseModel):
    table_count: int
    matched_tables: int
    source_only_tables: int
    target_only_tables: int
    tables_with_changes: int


class SchemaComparisonResult(BaseModel):
    source_id: int
    source_name: str
    target_id: int
    target_name: str
    tables: list[TableComparison]
    summary: ComparisonSummary
