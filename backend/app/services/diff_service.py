import logging
from typing import List, Dict, Any, Optional, Tuple, Set

logger = logging.getLogger(__name__)

class DiffService:
    @staticmethod
    def compare_tables(source_cols: List[Dict[str, Any]], target_cols: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Compares columns of two tables and returns structural diff.
        """
        source_map = {c["name"]: c for c in source_cols}
        target_map = {c["name"]: c for c in target_cols}

        matched = []
        missing_in_target = []
        missing_in_source = []
        type_mismatches = []

        for name, col in source_map.items():
            if name in target_map:
                target_col = target_map[name]
                matched.append(name)
                if col["type"].lower() != target_col["type"].lower():
                    type_mismatches.append({
                        "column": name,
                        "source_type": col["type"],
                        "target_type": target_col["type"]
                    })
            else:
                missing_in_target.append(name)

        for name in target_map.keys():
            if name not in source_map:
                missing_in_source.append(name)

        return {
            "matched": matched,
            "missing_in_target": missing_in_target,
            "missing_in_source": missing_in_source,
            "type_mismatches": type_mismatches,
            "score": len(matched) / max(1, len(source_map)) * 100
        }

    @staticmethod
    def compare_schemas(source_schema: Dict[str, List[Dict[str, Any]]], target_schema: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        Compares entire schemas and finds exact table matches and structural diffs.
        """
        source_tables = set(source_schema.keys())
        target_tables = set(target_schema.keys())

        matched_tables = source_tables.intersection(target_tables)
        missing_tables_in_target = list(source_tables - target_tables)
        missing_tables_in_source = list(target_tables - source_tables)

        table_diffs = {}
        for table in matched_tables:
            table_diffs[table] = DiffService.compare_tables(source_schema[table], target_schema[table])

        return {
            "matched_tables": list(matched_tables),
            "missing_tables_in_target": missing_tables_in_target,
            "missing_tables_in_source": missing_tables_in_source,
            "table_diffs": table_diffs
        }

    @staticmethod
    def generate_graph_data(
        source_schema: Dict[str, List[Dict[str, Any]]],
        target_schema: Dict[str, List[Dict[str, Any]]],
        diff_result: Dict[str, Any],
        classifications: Dict[str, Any] = None,
        ai_matches: List[Dict[str, Any]] = None,
        source_name: str = "Source",
        target_name: str = "Target",
        real_mappings: Dict[str, Any] = None,
        ai_match_pair: Optional[Tuple[str, str]] = None,
        classifications_target: Dict[str, Any] = None,
        drift_by_table: Dict[str, str] = None,
        drift_by_table_target: Dict[str, str] = None,
        last_refresh_by_table: Dict[str, str] = None,
        last_refresh_by_table_target: Dict[str, str] = None,
        ai_matches_by_pair: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Convert schema + diff + classification data into a graph-compatible format
        for Neo4j/NetworkX-style visualization on the frontend.

        ``real_mappings`` (source_table -> [{"target_table", "field_count"}]) comes
        from an actual published Schema Mapper mapping between the two
        connections, if one exists. Exact-name matching (``diff_result``) can't
        see a legitimate rename (e.g. crm_users -> dw_customers via a real ETL
        mapping) and would otherwise flag every renamed table as "not found" —
        real_mappings overrides that false positive and draws the real mapped
        edge instead of relying on name equality.

        ``ai_match_pair`` is the ``(source_table, target_table)`` the caller
        actually ran ``AIService.match_schemas`` against — without it, the
        AI-match edge can't be drawn between the correct two nodes.

        ``classifications_target`` mirrors ``classifications`` but for the
        target schema — used to populate per-column PII badges on target nodes
        (B04: symmetric classification).

        ``drift_by_table`` maps ``{table_name: "drifted" | "stable"}`` for any
        table that has a known drift status — consumed by E02-4 node indicators.
        """
        raw_real_mappings = real_mappings or {}
        # Backward-compatible normalization for direct service callers that
        # still pass the original one-target-per-source shape.
        real_mappings = {
            source_table: infos if isinstance(infos, list) else [infos]
            for source_table, infos in raw_real_mappings.items()
        }
        drift_by_table = drift_by_table or {}
        drift_by_table_target = drift_by_table_target or {}
        last_refresh_by_table = last_refresh_by_table or {}
        last_refresh_by_table_target = last_refresh_by_table_target or {}
        real_mapped_source_tables = set(real_mappings.keys())
        real_mapped_target_tables = {
            info["target_table"]
            for infos in real_mappings.values()
            for info in infos
        }
        ai_matches_by_pair = ai_matches_by_pair or []

        # Pre-compute the set of matched source tables (exact-name + real-mapped)
        # for lineage-confidence and missing-edge lookups.
        matched_source_set: Set[str] = set(diff_result.get("matched_tables", [])) | real_mapped_source_tables

        nodes = []
        edges = []
        annotations = []

        # ── Classification lookups (source + target) ──────────
        cls_lookup: Dict[str, Dict[str, Any]] = {}
        cls_target_lookup: Dict[str, Dict[str, Any]] = {}
        if classifications:
            for table, cols in classifications.items():
                for c in cols:
                    cls_lookup[f"{table}.{c['column']}"] = c["classification"]
        if classifications_target:
            for table, cols in classifications_target.items():
                for c in cols:
                    cls_target_lookup[f"{table}.{c['column']}"] = c["classification"]

        # ── Helpers for node indicators (B02) ─────────────────
        def _derive_sensitivity(table: str, columns: List[Dict[str, Any]], lookup: Dict[str, Dict[str, Any]]) -> Optional[str]:
            """Derive a table-level sensitivity from per-column classification levels."""
            has_high = False
            has_medium = False
            for col in columns:
                cls = lookup.get(f"{table}.{col['name']}", {})
                level = cls.get("level", "Low")
                if level == "High":
                    has_high = True
                    break
                elif level == "Medium":
                    has_medium = True
            if has_high:
                return "critical"
            if has_medium:
                return "medium"
            # Only emit sensitivity when at least one column has a non-default classification
            any_classified = any(lookup.get(f"{table}.{col['name']}") for col in columns)
            return "low" if any_classified else None

        def _lineage_confidence(table: str, group: str) -> Optional[float]:
            """Return the match score for a matched table, None otherwise."""
            if table in diff_result.get("matched_tables", []):
                td = diff_result.get("table_diffs", {}).get(table, {})
                score = td.get("score", 0)
                return round(score, 1) if score else None
            return None

        def _drift_status(table: str, group: str) -> Optional[str]:
            """Return 'drifted' or 'stable' from the pre-computed drift map."""
            lookup = drift_by_table if group == "source" else drift_by_table_target
            return lookup.get(table)

        def _risk_level(table: str, columns: List[Dict[str, Any]], lookup: Dict[str, Dict[str, Any]]) -> str:
            levels = [
                lookup.get(f"{table}.{column['name']}", {}).get("level", "Low")
                for column in columns
            ]
            if "High" in levels:
                return "high"
            if "Medium" in levels:
                return "medium"
            return "low"

        # ── Source table nodes ────────────────────────────────
        for i, (table, cols) in enumerate(source_schema.items()):
            # Table group node
            table_node_id = f"src_{table}"
            has_issues = (
                table in diff_result.get("missing_tables_in_target", [])
                and table not in real_mapped_source_tables
            )
            risk_level = _risk_level(table, cols, cls_lookup)

            nodes.append({
                "id": table_node_id,
                "label": table,
                "type": "table",
                "group": "source",
                "database": source_name,
                "columns": [
                    {
                        "name": c["name"],
                        "type": c.get("type", "?"),
                        "primary_key": c.get("primary_key", False),
                        "nullable": c.get("nullable", True),
                        "classification": cls_lookup.get(f"{table}.{c['name']}", {}),
                    }
                    for c in cols
                ],
                "risk_level": risk_level,
                "has_issues": has_issues,
                "column_count": len(cols),
                # E02-4 node indicators (B02)
                "sensitivity": _derive_sensitivity(table, cols, cls_lookup),
                "lineage_confidence": _lineage_confidence(table, "source"),
                "lineage_confirmed": table in real_mapped_source_tables,
                "drift_status": _drift_status(table, "source"),
                "last_refresh": last_refresh_by_table.get(table),
                "style": {
                    "background": "#1e293b" if not has_issues else "#7f1d1d",
                    "border": (
                        "#ef4444" if risk_level == "high"
                        else "#f59e0b" if risk_level == "medium"
                        else "#22c55e"
                    ),
                },
            })

            if has_issues:
                annotations.append({
                    "node_id": table_node_id,
                    "type": "error",
                    "message": f"Table '{table}' not found in target schema",
                    "severity": "high",
                })

        # ── Target table nodes ────────────────────────────────
        for i, (table, cols) in enumerate(target_schema.items()):
            table_node_id = f"tgt_{table}"
            has_issues = (
                table in diff_result.get("missing_tables_in_source", [])
                and table not in real_mapped_target_tables
            )

            # B04: symmetric classification — use target lookup
            tgt_cls = cls_target_lookup
            risk_level = _risk_level(table, cols, tgt_cls)

            nodes.append({
                "id": table_node_id,
                "label": table,
                "type": "table",
                "group": "target",
                "database": target_name,
                "columns": [
                    {
                        "name": c["name"],
                        "type": c.get("type", "?"),
                        "primary_key": c.get("primary_key", False),
                        "nullable": c.get("nullable", True),
                        "classification": tgt_cls.get(f"{table}.{c['name']}", {}),
                    }
                    for c in cols
                ],
                "risk_level": risk_level,
                "has_issues": has_issues,
                "column_count": len(cols),
                # E02-4 node indicators (B02)
                "sensitivity": _derive_sensitivity(table, cols, tgt_cls),
                "lineage_confidence": _lineage_confidence(table, "target"),
                "lineage_confirmed": table in real_mapped_target_tables,
                "drift_status": _drift_status(table, "target"),
                "last_refresh": last_refresh_by_table_target.get(table),
                "style": {
                    "background": "#1e293b" if not has_issues else "#7f1d1d",
                    "border": (
                        "#ef4444" if risk_level == "high"
                        else "#f59e0b" if risk_level == "medium" or has_issues
                        else "#3b82f6"
                    ),
                },
            })

            if has_issues:
                annotations.append({
                    "node_id": table_node_id,
                    "type": "error",
                    "message": f"Table '{table}' not found in source schema",
                    "severity": "high",
                })

        # ── Matched table edges ───────────────────────────────
        for table in diff_result.get("matched_tables", []):
            if table in real_mapped_source_tables:
                continue
            table_diff = diff_result.get("table_diffs", {}).get(table, {})
            score = table_diff.get("score", 0)
            edges.append({
                "source": f"src_{table}",
                "target": f"tgt_{table}",
                "type": "exact_match",
                "edge_type": "exact",
                "confidence": round(score, 1),
                "label": f"{score:.0f}% match",
                "animated": True,
                "style": {
                    "stroke": "#22c55e" if score > 80 else "#f59e0b" if score > 50 else "#ef4444",
                },
            })

            # Type mismatch annotations
            for tm in table_diff.get("type_mismatches", []):
                annotations.append({
                    "node_id": f"src_{table}",
                    "type": "warning",
                    "message": f"Type mismatch: {tm['column']} ({tm['source_type']} → {tm['target_type']})",
                    "severity": "medium",
                })

        # ── Real published-mapping edges ───────────────────────
        # These reflect an actual ETL mapping (Schema Mapper), so they can
        # legitimately connect tables with different names — the label
        # states field count rather than a name/type "% match" score, since
        # a published mapping's transformations can rename/retype columns
        # on purpose.
        for source_table, infos in real_mappings.items():
            for info in infos:
                field_count = info["field_count"]
                transformed = bool(info.get("has_transformation"))
                edges.append({
                    "source": f"src_{source_table}",
                    "target": f"tgt_{info['target_table']}",
                    "type": "published_mapping",
                    "edge_type": "transformation" if transformed else "business_rule",
                    "confidence": None,
                    "label": (
                        f"Transformed ({field_count} field{'s' if field_count != 1 else ''})"
                        if transformed
                        else f"Mapped ({field_count} field{'s' if field_count != 1 else ''})"
                    ),
                    "animated": True,
                    "style": {"stroke": "#ea580c" if transformed else "#7c3aed"},
                })

        # ── Missing-table edges (B01) ─────────────────────────
        # Never point a missing relationship at an arbitrary real table: that
        # invents lineage. A small placeholder represents the absent endpoint.
        for table in diff_result.get("missing_tables_in_target", []):
            if table in real_mapped_source_tables:
                continue
            placeholder_id = f"missing_tgt_{table}"
            nodes.append({
                "id": placeholder_id, "label": f"{table} (missing)",
                "type": "table", "group": "target", "database": target_name,
                "columns": [], "risk_level": "high", "has_issues": True,
                "column_count": 0, "is_placeholder": True,
            })
            edges.append({
                "source": f"src_{table}", "target": placeholder_id,
                "type": "missing_table", "edge_type": "missing",
                "confidence": None, "label": "Missing in target",
                "animated": False, "style": {"stroke": "#ef4444", "strokeDasharray": "4 4"},
            })

        for table in diff_result.get("missing_tables_in_source", []):
            if table in real_mapped_target_tables:
                continue
            placeholder_id = f"missing_src_{table}"
            nodes.append({
                "id": placeholder_id, "label": f"{table} (missing)",
                "type": "table", "group": "source", "database": source_name,
                "columns": [], "risk_level": "high", "has_issues": True,
                "column_count": 0, "is_placeholder": True,
            })
            edges.append({
                "source": placeholder_id, "target": f"tgt_{table}",
                "type": "missing_table", "edge_type": "missing",
                "confidence": None, "label": "Missing in source",
                "animated": False, "style": {"stroke": "#ef4444", "strokeDasharray": "4 4"},
            })

        # ── AI-matched edges ──────────────────────────────────
        # ai_match_pair names the exact two nodes the caller ran
        # AIService.match_schemas against — previously this always drew an
        # edge between the first source and first target node regardless of
        # which pair was actually matched, which mislabeled unrelated tables.
        if ai_matches and ai_match_pair:
            src_id, tgt_id = f"src_{ai_match_pair[0]}", f"tgt_{ai_match_pair[1]}"
            # ReactFlow routes table-level edges by endpoint. Emitting one edge
            # per matched column draws every path and label on top of the same
            # table pair, producing an unreadable/corrupted graph. Preserve the
            # column evidence on one aggregate edge instead.
            confidences = [float(match.get("confidence", 0)) for match in ai_matches]
            confidence = round(sum(confidences) / len(confidences), 1)
            match_count = len(ai_matches)
            edges.append({
                "source": src_id,
                "target": tgt_id,
                "type": "ai_match",
                "edge_type": "ai",
                "confidence": confidence,
                "label": (
                    f"Suggested: {match_count} column"
                    f"{'s' if match_count != 1 else ''} ({confidence:.0f}%)"
                ),
                "column_matches": ai_matches,
                "animated": True,
                "style": {
                    "stroke": "#8b5cf6",
                    "strokeDasharray": "5,5",
                },
            })

        for pair in ai_matches_by_pair:
            pair_matches = pair.get("matches") or []
            if not pair_matches:
                continue
            confidences = [float(match.get("confidence", 0)) for match in pair_matches]
            confidence = round(sum(confidences) / len(confidences), 1)
            match_count = len(pair_matches)
            edges.append({
                "source": f"src_{pair['source_table']}",
                "target": f"tgt_{pair['target_table']}",
                "type": "ai_match", "edge_type": "ai",
                "confidence": confidence,
                "label": f"Suggested: {match_count} column{'s' if match_count != 1 else ''} ({confidence:.0f}%)",
                "column_matches": pair_matches,
                "animated": True,
                "style": {"stroke": "#8b5cf6", "strokeDasharray": "5,5"},
            })

        # ── Summary stats ─────────────────────────────────────
        # Counted from the actual per-node has_issues flags (which factor in
        # real_mappings) rather than the raw name-only diff_result, so the
        # summary tile numbers always match what the graph visibly shows.
        source_issue_count = sum(
            1 for n in nodes
            if n["group"] == "source" and n["has_issues"] and not n.get("is_placeholder")
        )
        target_issue_count = sum(
            1 for n in nodes
            if n["group"] == "target" and n["has_issues"] and not n.get("is_placeholder")
        )
        summary = {
            "total_source_tables": len(source_schema),
            "total_target_tables": len(target_schema),
            "matched_tables": len(
                set(diff_result.get("matched_tables", [])) | real_mapped_source_tables
            ),
            "missing_in_target": source_issue_count,
            "missing_in_source": target_issue_count,
            "total_annotations": len(annotations),
            "high_risk_count": sum(1 for a in annotations if a.get("severity") == "high"),
            "medium_risk_count": sum(1 for a in annotations if a.get("severity") == "medium"),
        }

        return {
            "nodes": nodes,
            "edges": edges,
            "annotations": annotations,
            "summary": summary,
        }
