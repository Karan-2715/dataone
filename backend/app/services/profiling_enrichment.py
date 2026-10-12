"""Profiling enrichment computations (agentic_dba_tasks #2).

Pure/bounded helpers layered on top of the existing profiling pass:
uniqueness ratio, duplicate counting from the in-memory sample, and
FK-candidate inference by value overlap against other tables' declared
primary keys. All heuristics report a confidence ratio, never a boolean —
downstream consumers (DQ rule proposal, plan review UI) must present these
as hints, not asserted facts.

Data-safety invariant (Schema Intel Task #8 Decision 1): sample values are
consumed in-memory here and only aggregates leave this module — no sampled
value is returned, persisted, or logged.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.schema_catalog import CatalogColumn, CatalogTable

logger = logging.getLogger(__name__)


def compute_uniqueness_ratio(distinct_count: Optional[int],
                             row_count: Optional[int],
                             scanned_rows: Optional[int] = None) -> Optional[float]:
    """Fraction of the profiled population that is distinct.

    `distinct_count` is computed over the first `scanned_rows` rows (the
    connector bounds the DISTINCT scan by a limit), while `row_count` is the
    full table size. Dividing by the full `row_count` would understate
    uniqueness on any table larger than the scan limit — a genuinely-unique
    key column on a 1M-row table with a 100k scan cap would read as 0.1,
    silently disabling the unique/dedupe DQ rules on exactly the large tables
    they matter for. So the denominator is the size of the population the
    distinct count was actually measured over: min(row_count, scanned_rows).
    """
    if distinct_count is None or not row_count or row_count <= 0:
        return None
    denominator = row_count
    if scanned_rows is not None and scanned_rows > 0:
        denominator = min(row_count, scanned_rows)
    return round(min(1.0, distinct_count / denominator), 4)


def count_duplicate_values(sample_values: List[Any]) -> int:
    """Number of distinct non-null sampled values that appear more than once.

    An aggregate only — the duplicated values themselves never leave the
    caller's memory.
    """
    counts = Counter(v for v in sample_values if v is not None)
    return sum(1 for n in counts.values() if n > 1)


def _run_select(connector, sql: str) -> List[Any]:
    """Run a SELECT through whatever query surface the connector exposes,
    returning a flat list of first-column values. Mirrors
    NL2SQLService.execute_safe_query's connector handling."""
    if hasattr(connector, "execute_query"):
        rows = connector.execute_query(sql)
        return [next(iter(r.values())) for r in rows if r]
    conn = connector.connect()
    cur = conn.cursor()
    cur.execute(sql)
    return [row[0] for row in cur.fetchall()]


def infer_fk_candidates(
    db: Session,
    connection_id: int,
    connector,
    table_name: str,
    column_name: str,
    sample_values: List[Any],
    *,
    db_type: str = "sqlite",
    max_tables: int = 25,
    pk_value_limit: int = 10000,
    min_overlap: float = 0.5,
) -> List[Dict[str, Any]]:
    """Compare this column's sampled values against other tables' declared
    PK columns in the same connection; return candidates above min_overlap.

    Bounded on purpose: only declared PK columns are compared (never every
    column of every table), at most max_tables comparisons, at most
    pk_value_limit PK values fetched per comparison. Values are normalized
    to strings so INTEGER ids overlap with TEXT ids across dialects.
    """
    distinct_sample = {str(v) for v in sample_values if v is not None}
    if not distinct_sample:
        return []

    quote = "`" if db_type == "mysql" else '"'

    pk_columns = (
        db.query(CatalogColumn, CatalogTable)
        .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
        .filter(
            CatalogTable.connection_id == connection_id,
            CatalogColumn.is_primary_key.is_(True),
            CatalogTable.table_name != table_name,
        )
        .limit(max_tables)
        .all()
    )

    candidates: List[Dict[str, Any]] = []
    for col, table in pk_columns:
        t = quote + table.table_name.replace(quote, quote * 2) + quote
        c = quote + col.column_name.replace(quote, quote * 2) + quote
        if db_type == "oracle":
            bound_sql = (f"SELECT DISTINCT {c} FROM {t} "
                         f"FETCH FIRST {int(pk_value_limit)} ROWS ONLY")
        else:
            bound_sql = f"SELECT DISTINCT {c} FROM {t} LIMIT {int(pk_value_limit)}"
        try:
            values = _run_select(connector, bound_sql)
        except Exception as exc:
            logger.debug("[profiling] fk-candidate fetch failed table=%s column=%s error=%s",
                         table.table_name, col.column_name, exc)
            continue
        pk_values = {str(v) for v in values if v is not None}
        if not pk_values:
            continue
        overlap = len(distinct_sample & pk_values) / len(distinct_sample)
        if overlap >= min_overlap:
            candidates.append({
                "table": table.table_name,
                "column": col.column_name,
                "overlap_ratio": round(overlap, 3),
            })

    return sorted(candidates, key=lambda cand: -cand["overlap_ratio"])
