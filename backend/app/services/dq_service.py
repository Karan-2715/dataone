"""Data Quality Observatory — scorecards from existing profiling data
(Enterprise v2, E06-1).

This computes scores; it never re-profiles. All inputs come from
``ColumnProfile`` (populated by schema_intel_tasks.profile_connection_task).
Only two of the vision's five DQ dimensions are computable from what the
platform already measures:

    completeness = 1 - null_rate                              (real)
    uniqueness   = uniqueness_ratio                            (real)
    consistency  = 1 - duplicate_count / row_count             (real, proxy —
                   the DB has no format/type-conformance signal today, so
                   this uses the same "duplicates indicate inconsistency"
                   reading dq_rule_proposer.py's dedupe rule already relies on)

Accuracy and freshness have no data source in this codebase yet — they are
never fabricated; the API returns them as null and the UI must render an
explicit "not yet available" state, never a number.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.connection import DBConnection
from app.models.schema_catalog import CatalogColumn, CatalogTable, ColumnProfile

logger = logging.getLogger(__name__)


def _round_pct(value: float) -> float:
    return round(value * 100, 1)


def _column_scores(profile: ColumnProfile | None) -> dict[str, float | None]:
    if profile is None:
        return {"completeness": None, "uniqueness": None, "consistency": None}

    completeness = _round_pct(1 - profile.null_rate) if profile.null_rate is not None else None
    uniqueness = _round_pct(profile.uniqueness_ratio) if profile.uniqueness_ratio is not None else None

    consistency = None
    if profile.duplicate_count is not None and profile.row_count:
        consistency = _round_pct(max(0.0, 1 - profile.duplicate_count / profile.row_count))

    return {"completeness": completeness, "uniqueness": uniqueness, "consistency": consistency}


def _overall(scores: dict[str, float | None]) -> float | None:
    present = [v for v in scores.values() if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 1)


class DataQualityService:
    def __init__(self, db: Session):
        self.db = db

    def get_scorecard(self, connection_id: int) -> dict[str, Any] | None:
        conn = self.db.query(DBConnection).filter(
            DBConnection.id == connection_id,
            DBConnection.is_deleted.is_(False),
        ).first()
        if conn is None:
            return None

        rows = (
            self.db.query(CatalogTable, CatalogColumn, ColumnProfile)
            .join(CatalogColumn, CatalogColumn.table_id == CatalogTable.id)
            .outerjoin(ColumnProfile, ColumnProfile.column_id == CatalogColumn.id)
            .filter(CatalogTable.connection_id == connection_id)
            .all()
        )

        return self._build_scorecard(conn.id, conn.name, rows)

    @staticmethod
    def _build_scorecard(
        connection_id: int,
        connection_name: str,
        rows: list[tuple[CatalogTable, CatalogColumn, ColumnProfile | None]],
    ) -> dict[str, Any]:
        tables: dict[str, dict[str, Any]] = {}
        for table, column, profile in rows:
            entry = tables.setdefault(table.table_name, {
                "table": table.table_name, "columns": [], "last_scanned_at": table.last_scanned_at,
            })
            scores = _column_scores(profile)
            overall = _overall(scores)
            entry["columns"].append({
                "column": column.column_name,
                "completeness": scores["completeness"],
                "uniqueness": scores["uniqueness"],
                "consistency": scores["consistency"],
                "accuracy": None,   # no data source yet — never fabricated
                "freshness": None,  # no data source yet — never fabricated
                "overall": overall,
                "profiled": profile is not None,
            })

        table_scorecards = []
        connection_scores: list[float] = []
        profiled_total = 0
        column_total = 0
        for table_name, entry in tables.items():
            cols = entry["columns"]
            column_total += len(cols)
            profiled_cols = [c for c in cols if c["profiled"]]
            profiled_total += len(profiled_cols)
            scored = [c["overall"] for c in cols if c["overall"] is not None]
            table_overall = round(sum(scored) / len(scored), 1) if scored else None
            if table_overall is not None:
                connection_scores.append(table_overall)
            table_scorecards.append({
                "table": table_name,
                "column_count": len(cols),
                "profiled_column_count": len(profiled_cols),
                "overall": table_overall,
                "columns": cols,
                "last_scanned_at": entry["last_scanned_at"],
            })

        table_scorecards.sort(key=lambda t: (t["overall"] if t["overall"] is not None else 101))

        return {
            "connection_id": connection_id,
            "connection_name": connection_name,
            "overall": round(sum(connection_scores) / len(connection_scores), 1) if connection_scores else None,
            "table_count": len(table_scorecards),
            "column_count": column_total,
            "profiled_column_count": profiled_total,
            "tables": table_scorecards,
        }

    def get_all_scorecards_summary(self) -> dict[str, Any]:
        """Aggregate view across every active connection — powers the E03
        DQ KPI without requiring a specific connection_id."""
        conns = self.db.query(DBConnection).filter(DBConnection.is_deleted.is_(False)).all()
        connection_ids = [conn.id for conn in conns]
        rows = []
        if connection_ids:
            rows = (
                self.db.query(CatalogTable, CatalogColumn, ColumnProfile)
                .join(CatalogColumn, CatalogColumn.table_id == CatalogTable.id)
                .outerjoin(ColumnProfile, ColumnProfile.column_id == CatalogColumn.id)
                .filter(CatalogTable.connection_id.in_(connection_ids))
                .all()
            )
        rows_by_connection: dict[int, list[tuple[CatalogTable, CatalogColumn, ColumnProfile | None]]] = {
            connection_id: [] for connection_id in connection_ids
        }
        for row in rows:
            rows_by_connection[row[0].connection_id].append(row)
        scores: list[float] = []
        profiled = 0
        total = 0
        for conn in conns:
            card = self._build_scorecard(conn.id, conn.name, rows_by_connection[conn.id])
            if card["overall"] is not None:
                scores.append(card["overall"])
            profiled += card["profiled_column_count"]
            total += card["column_count"]
        return {
            "overall": round(sum(scores) / len(scores), 1) if scores else None,
            "connection_count": len(conns),
            "profiled_column_count": profiled,
            "column_count": total,
        }
