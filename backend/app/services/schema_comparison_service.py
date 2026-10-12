"""Schema Comparison Mode (Enterprise v2, E12).

Extends DiffService.compare_schemas into a column-aligned, side-by-side
structure — reuses the existing diff logic entirely (table-level
matching, type mismatches) and adds constraint-level diffing (nullable,
primary key) that DiffService doesn't compute today. Does not
reimplement table matching.
"""
from __future__ import annotations

from typing import Any

from app.services.diff_service import DiffService


def _column_map(columns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in columns}


class SchemaComparisonService:
    @staticmethod
    def compare(
        source_schema: dict[str, list[dict[str, Any]]],
        target_schema: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        diff = DiffService.compare_schemas(source_schema, target_schema)
        all_tables = sorted(set(source_schema) | set(target_schema))

        tables: list[dict[str, Any]] = []
        for table in all_tables:
            if table in diff["missing_tables_in_target"]:
                tables.append({
                    "table": table, "status": "source_only",
                    "added_columns": [], "missing_columns": [],
                    "changed_types": [], "changed_constraints": [],
                    "column_count": len(source_schema[table]),
                })
                continue
            if table in diff["missing_tables_in_source"]:
                tables.append({
                    "table": table, "status": "target_only",
                    "added_columns": [], "missing_columns": [],
                    "changed_types": [], "changed_constraints": [],
                    "column_count": len(target_schema[table]),
                })
                continue

            src_cols = _column_map(source_schema[table])
            tgt_cols = _column_map(target_schema[table])
            table_diff = diff["table_diffs"].get(table, {})

            added_columns = sorted(set(tgt_cols) - set(src_cols))
            missing_columns = sorted(set(src_cols) - set(tgt_cols))
            changed_types = table_diff.get("type_mismatches", [])

            changed_constraints = []
            for name in sorted(set(src_cols) & set(tgt_cols)):
                s, t = src_cols[name], tgt_cols[name]
                s_nullable, t_nullable = s.get("nullable"), t.get("nullable")
                s_pk, t_pk = bool(s.get("primary_key")), bool(t.get("primary_key"))
                if s_nullable != t_nullable or s_pk != t_pk:
                    changed_constraints.append({
                        "column": name,
                        "source_nullable": s_nullable, "target_nullable": t_nullable,
                        "source_primary_key": s_pk, "target_primary_key": t_pk,
                    })

            tables.append({
                "table": table, "status": "matched",
                "added_columns": added_columns,
                "missing_columns": missing_columns,
                "changed_types": changed_types,
                "changed_constraints": changed_constraints,
                # build-validation B-E12-08: source-only column count
                # under-reported the total whenever the target had added
                # columns — the union of both sides is the real count.
                "column_count": len(set(src_cols) | set(tgt_cols)),
            })

        summary = {
            "table_count": len(tables),
            "matched_tables": sum(1 for t in tables if t["status"] == "matched"),
            "source_only_tables": sum(1 for t in tables if t["status"] == "source_only"),
            "target_only_tables": sum(1 for t in tables if t["status"] == "target_only"),
            "tables_with_changes": sum(
                1 for t in tables
                if t["added_columns"] or t["missing_columns"] or t["changed_types"] or t["changed_constraints"]
            ),
        }
        return {"tables": tables, "summary": summary}
