"""Data-quality rule proposal from real profiling statistics (agentic_dba_tasks #4).

Every proposed rule cites the exact profiling number that justifies it and
carries a confidence — "based on profiling" must be a defensible claim in
the plan-review UI, never decoration. No profile for a source column → no
rule, with an explicit confidence note (never a silent guess).

Thresholds below are documented judgment calls (task #4 risk note) — pick
defensible starting values, expect tuning after real usage:
    NOT NULL     null_rate <= 1%         (a near-zero rate over the sample)
    UNIQUE       uniqueness_ratio >= 99% ("appears unique in the profiled
                                          sample" — a sample is NOT a
                                          full-table guarantee)
    FOREIGN KEY  fk overlap_ratio >= 80% (inferred, not verified)
    DEDUPE       duplicate_count > 0 on a near-unique column — a load-time
                 step, not a target constraint (constraints don't fix
                 existing duplicate source data)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.schema_catalog import CatalogColumn, CatalogTable, ColumnProfile

logger = logging.getLogger(__name__)

NOT_NULL_MAX_NULL_RATE = 0.01
UNIQUE_MIN_RATIO = 0.99
FK_MIN_OVERLAP = 0.8


def _profile_for(db: Session, connection_id: int,
                 table: str, column: str) -> Optional[ColumnProfile]:
    return (
        db.query(ColumnProfile)
        .join(CatalogColumn, ColumnProfile.column_id == CatalogColumn.id)
        .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
        .filter(CatalogTable.connection_id == connection_id,
                CatalogTable.table_name == table,
                CatalogColumn.column_name == column)
        .first()
    )


def propose_dq_rules(
    db: Session,
    connection_id: int,
    proposed_tables: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Returns (dq_rules, confidence_notes)."""
    rules: List[Dict[str, Any]] = []
    notes: List[str] = []

    for table in proposed_tables:
        for col in table.get("columns", []):
            source_refs = col.get("source_refs") or []
            if not source_refs:
                continue
            src = source_refs[0]  # rules ground in the primary source column
            profile = _profile_for(db, connection_id, src["table"], src["column"])
            if profile is None:
                notes.append(
                    f"no profile for {src['table']}.{src['column']} — scan/profile "
                    f"this connection to get DQ suggestions for "
                    f"{table['name']}.{col['name']}"
                )
                continue

            sampled = profile.row_count if profile.row_count is not None else profile.sample_size_used

            if profile.null_rate is not None and profile.null_rate <= NOT_NULL_MAX_NULL_RATE:
                rules.append({
                    "rule": "not_null",
                    "target_table": table["name"],
                    "target_column": col["name"],
                    "source": src,
                    "justification": (
                        f"{profile.null_rate:.2%} null over {sampled} profiled rows "
                        f"in {src['table']}.{src['column']}"
                    ),
                    "confidence": round(1.0 - profile.null_rate, 3),
                })

            if profile.uniqueness_ratio is not None and profile.uniqueness_ratio >= UNIQUE_MIN_RATIO:
                rules.append({
                    "rule": "unique",
                    "target_table": table["name"],
                    "target_column": col["name"],
                    "source": src,
                    "justification": (
                        f"appears unique in the profiled sample "
                        f"(uniqueness ratio {profile.uniqueness_ratio:.2%} over "
                        f"{sampled} rows) — a sample is not a full-table guarantee"
                    ),
                    "confidence": round(profile.uniqueness_ratio, 3),
                })

            for cand in (profile.fk_candidates or []):
                if cand.get("overlap_ratio", 0) >= FK_MIN_OVERLAP:
                    rules.append({
                        "rule": "foreign_key",
                        "target_table": table["name"],
                        "target_column": col["name"],
                        "source": src,
                        "references": {"table": cand["table"], "column": cand["column"]},
                        "justification": (
                            f"inferred FK to {cand['table']}.{cand['column']} at "
                            f"{cand['overlap_ratio']:.0%} value overlap — inferred, not verified"
                        ),
                        "confidence": round(cand["overlap_ratio"], 3),
                    })

            if (profile.duplicate_count or 0) > 0 and (
                profile.uniqueness_ratio is not None
                and profile.uniqueness_ratio >= 0.9
                and profile.uniqueness_ratio < 1.0
            ):
                rules.append({
                    "rule": "dedupe",
                    "target_table": table["name"],
                    "target_column": col["name"],
                    "source": src,
                    "justification": (
                        f"{profile.duplicate_count} duplicated value(s) found in the "
                        f"profiled sample of {src['table']}.{src['column']} — propose a "
                        f"dedup step ahead of load (a target constraint won't fix "
                        f"existing duplicate source data)"
                    ),
                    "confidence": 0.7,
                })

    return rules, notes
