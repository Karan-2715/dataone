"""Data-quality profile of an uploaded file, computed from the parsed rows before they are written.

Definitions match ``databricks_assets/notebooks/dataone_data_quality.py`` (0-100):
  completeness — % of cells present (NULL / blank counts as missing)
  uniqueness   — 100 - % of rows that are exact duplicates of an earlier row
Validity and freshness are not measured here (no type or timestamp information in a text upload).
"""
from typing import Any, Dict, List, Optional

MISSING_ISSUE_THRESHOLD_PCT = 5.0
MAX_ISSUES = 10


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 100.0


def profile_rows(columns: List[str], rows: List[Dict[str, Optional[str]]]) -> Dict[str, Any]:
    total_rows = len(rows)
    missing_by_col = {
        c: sum(1 for r in rows if r.get(c) is None or not str(r.get(c)).strip()) for c in columns
    }
    total_cells = total_rows * len(columns)
    missing_cells = sum(missing_by_col.values())

    seen = set()
    duplicates = 0
    for r in rows:
        key = tuple(r.get(c) for c in columns)
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)

    issues = []
    for c, missing in sorted(missing_by_col.items(), key=lambda kv: kv[1], reverse=True):
        missing_pct = _pct(missing, total_rows)
        if missing == total_rows and total_rows:
            issues.append({"column": c, "check": "completeness", "detail": "column is entirely empty"})
        elif missing_pct > MISSING_ISSUE_THRESHOLD_PCT:
            issues.append({"column": c, "check": "completeness", "detail": f"{missing} of {total_rows} values missing ({missing_pct}%)"})
    if duplicates:
        issues.append({"column": None, "check": "uniqueness", "detail": f"{duplicates} duplicate rows"})

    return {
        "row_count": total_rows,
        "column_count": len(columns),
        "completeness": _pct(total_cells - missing_cells, total_cells),
        "uniqueness": _pct(total_rows - duplicates, total_rows),
        "duplicate_rows": duplicates,
        "columns": [
            {"column": c, "missing": m, "completeness": _pct(total_rows - m, total_rows)}
            for c, m in missing_by_col.items()
        ],
        "issues": issues[:MAX_ISSUES],
    }


def to_dq_run(catalog: str, schema_name: str, table_name: str, quality: Dict[str, Any]):
    """Build the dashboard payload for an upload. One run per table, so re-uploading replaces it."""
    from app.schemas.data_quality import DQIssue, DQRunIn, DQTableIn

    return DQRunIn(
        run_id=f"upload:{catalog}.{schema_name}.{table_name}",
        source="upload",
        catalog=catalog,
        schema_name=schema_name,
        tables=[DQTableIn(
            table_name=f"{schema_name}.{table_name}",
            row_count=quality["row_count"],
            column_count=quality["column_count"],
            completeness=quality["completeness"],
            uniqueness=quality["uniqueness"],
            validity=None,  # not inferable from a text upload — never fabricated
            freshness=100.0,  # the table was written just now
            issues=[DQIssue(column=i["column"], check=i["check"], detail=i["detail"][:500]) for i in quality["issues"][:20]],
        )],
    )
