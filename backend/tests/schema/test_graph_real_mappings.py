"""Tests for the Schema Topology graph fix: real published Schema Mapper
mappings must override exact-name-only false positives (bug: a renamed
table with a real, working, published mapping — e.g. crm_users ->
dw_customers — was flagged "not found in target schema", and the AI-match
edge always connected the first source/target node regardless of which
tables were actually compared).
"""
import pytest

from app.api.routers.schema import _get_real_table_mappings
from app.services.diff_service import DiffService


SOURCE_SCHEMA = {
    "users": [
        {"name": "id", "type": "INTEGER"},
        {"name": "name", "type": "TEXT"},
        {"name": "email", "type": "TEXT"},
    ],
    "leads": [
        {"name": "id", "type": "INTEGER"},
        {"name": "company", "type": "TEXT"},
    ],
}
TARGET_SCHEMA = {
    "customers": [
        {"name": "cust_id", "type": "INTEGER"},
        {"name": "full_name", "type": "TEXT"},
        {"name": "contact_email", "type": "TEXT"},
    ],
}


def _diff():
    return DiffService.compare_schemas(SOURCE_SCHEMA, TARGET_SCHEMA)


class TestGenerateGraphDataRealMappings:
    def test_without_real_mappings_renamed_table_flagged_not_found(self):
        """Baseline: exact-name matching alone can't see a rename."""
        graph = DiffService.generate_graph_data(SOURCE_SCHEMA, TARGET_SCHEMA, _diff())
        users_node = next(n for n in graph["nodes"] if n["id"] == "src_users")
        assert users_node["has_issues"] is True
        assert any("users" in a["message"] for a in graph["annotations"])

    def test_real_mapping_suppresses_false_not_found(self):
        real_mappings = {"users": {"target_table": "customers", "field_count": 3}}
        graph = DiffService.generate_graph_data(
            SOURCE_SCHEMA, TARGET_SCHEMA, _diff(), real_mappings=real_mappings,
        )
        users_node = next(n for n in graph["nodes"] if n["id"] == "src_users")
        customers_node = next(n for n in graph["nodes"] if n["id"] == "tgt_customers")
        assert users_node["has_issues"] is False
        assert customers_node["has_issues"] is False
        assert not any("'users'" in a["message"] for a in graph["annotations"])

    def test_real_mapping_draws_published_mapping_edge_with_field_count(self):
        real_mappings = {"users": {"target_table": "customers", "field_count": 3}}
        graph = DiffService.generate_graph_data(
            SOURCE_SCHEMA, TARGET_SCHEMA, _diff(), real_mappings=real_mappings,
        )
        edge = next(e for e in graph["edges"] if e["type"] == "published_mapping")
        assert edge["source"] == "src_users"
        assert edge["target"] == "tgt_customers"
        assert edge["label"] == "Mapped (3 fields)"

    def test_unmapped_table_still_flagged(self):
        """'leads' has no real mapping and no name match — still a real gap."""
        real_mappings = {"users": {"target_table": "customers", "field_count": 3}}
        graph = DiffService.generate_graph_data(
            SOURCE_SCHEMA, TARGET_SCHEMA, _diff(), real_mappings=real_mappings,
        )
        leads_node = next(n for n in graph["nodes"] if n["id"] == "src_leads")
        assert leads_node["has_issues"] is True
        assert any("'leads'" in a["message"] for a in graph["annotations"])

    def test_summary_counts_reflect_real_mappings(self):
        real_mappings = {"users": {"target_table": "customers", "field_count": 3}}
        graph = DiffService.generate_graph_data(
            SOURCE_SCHEMA, TARGET_SCHEMA, _diff(), real_mappings=real_mappings,
        )
        # matched via real_mappings (users) + 0 exact-name matches; only
        # 'leads' remains genuinely missing.
        assert graph["summary"]["matched_tables"] == 1
        assert graph["summary"]["missing_in_target"] == 1
        assert graph["summary"]["missing_in_source"] == 0

    def test_ai_match_edge_connects_the_actual_compared_pair_not_first_nodes(self):
        """Regression test for the bug where the AI-match edge always
        connected the first source node to the first target node,
        regardless of which two tables AIService.match_schemas actually
        compared."""
        ai_matches = [{"source": "company", "target": "full_name", "confidence": 42}]
        graph = DiffService.generate_graph_data(
            SOURCE_SCHEMA, TARGET_SCHEMA, _diff(),
            ai_matches=ai_matches, ai_match_pair=("leads", "customers"),
        )
        ai_edges = [e for e in graph["edges"] if e["type"] == "ai_match"]
        assert len(ai_edges) == 1
        assert ai_edges[0]["source"] == "src_leads"
        assert ai_edges[0]["target"] == "tgt_customers"

    def test_column_suggestions_are_aggregated_into_one_table_edge(self):
        ai_matches = [
            {"source": "id", "target": "cust_id", "confidence": 90},
            {"source": "company", "target": "full_name", "confidence": 70},
        ]
        graph = DiffService.generate_graph_data(
            SOURCE_SCHEMA, TARGET_SCHEMA, _diff(),
            ai_matches=ai_matches, ai_match_pair=("leads", "customers"),
        )

        ai_edges = [edge for edge in graph["edges"] if edge["edge_type"] == "ai"]
        assert len(ai_edges) == 1
        assert ai_edges[0]["confidence"] == 80.0
        assert ai_edges[0]["label"] == "Suggested: 2 columns (80%)"
        assert ai_edges[0]["column_matches"] == ai_matches

    def test_no_ai_match_edge_without_ai_match_pair(self):
        """If the caller didn't say which pair it compared, don't guess —
        previously this silently wired the edge to the first node pair."""
        ai_matches = [{"source": "company", "target": "full_name", "confidence": 42}]
        graph = DiffService.generate_graph_data(SOURCE_SCHEMA, TARGET_SCHEMA, _diff(), ai_matches=ai_matches)
        assert not any(e["type"] == "ai_match" for e in graph["edges"])

    def test_edge_contract_uses_five_color_vocabulary(self):
        graph = DiffService.generate_graph_data(SOURCE_SCHEMA, TARGET_SCHEMA, _diff())
        missing_edges = [edge for edge in graph["edges"] if edge["edge_type"] == "missing"]
        assert missing_edges
        assert all(edge["animated"] is False for edge in missing_edges)
        assert all(edge["edge_type"] in {
            "exact", "ai", "transformation", "missing", "business_rule",
        } for edge in graph["edges"])

    def test_missing_edges_use_absent_endpoint_placeholders_not_real_tables(self):
        graph = DiffService.generate_graph_data(SOURCE_SCHEMA, TARGET_SCHEMA, _diff())
        leads_edge = next(edge for edge in graph["edges"] if edge["source"] == "src_leads")
        placeholder = next(node for node in graph["nodes"] if node["id"] == leads_edge["target"])
        assert placeholder["id"] == "missing_tgt_leads"
        assert placeholder["is_placeholder"] is True
        assert placeholder["label"] == "leads (missing)"

    def test_source_and_target_indicators_use_their_own_signals(self):
        schema = {"users": SOURCE_SCHEMA["users"]}
        target = {"users": SOURCE_SCHEMA["users"]}
        classifications_source = {
            "users": [{"column": "email", "classification": {"label": "PII", "level": "High"}}],
        }
        classifications_target = {
            "users": [{"column": "name", "classification": {"label": "Sensitive", "level": "Medium"}}],
        }
        graph = DiffService.generate_graph_data(
            schema, target, DiffService.compare_schemas(schema, target),
            classifications=classifications_source,
            classifications_target=classifications_target,
            drift_by_table={"users": "drifted"},
            drift_by_table_target={"users": "stable"},
            last_refresh_by_table={"users": "2026-07-17T01:00:00+00:00"},
            last_refresh_by_table_target={"users": "2026-07-17T02:00:00+00:00"},
        )
        source = next(node for node in graph["nodes"] if node["id"] == "src_users")
        target_node = next(node for node in graph["nodes"] if node["id"] == "tgt_users")
        assert source["sensitivity"] == "critical"
        assert source["risk_level"] == "high"
        assert source["drift_status"] == "drifted"
        assert source["last_refresh"] == "2026-07-17T01:00:00+00:00"
        assert target_node["sensitivity"] == "medium"
        assert target_node["risk_level"] == "medium"
        assert target_node["drift_status"] == "stable"
        assert target_node["last_refresh"] == "2026-07-17T02:00:00+00:00"
        assert target_node["lineage_confidence"] == source["lineage_confidence"] == 100.0
        target_name = next(column for column in target_node["columns"] if column["name"] == "name")
        assert target_name["classification"]["label"] == "Sensitive"

    def test_exact_published_pair_is_not_double_drawn_or_counted(self):
        schema = {"users": SOURCE_SCHEMA["users"]}
        diff = DiffService.compare_schemas(schema, schema)
        graph = DiffService.generate_graph_data(
            schema, schema, diff,
            real_mappings={"users": {"target_table": "users", "field_count": 3}},
        )
        pair_edges = [
            edge for edge in graph["edges"]
            if edge["source"] == "src_users" and edge["target"] == "tgt_users"
        ]
        assert len(pair_edges) == 1
        assert pair_edges[0]["edge_type"] == "business_rule"
        assert graph["summary"]["matched_tables"] == 1


class TestGetRealTableMappings:
    def test_groups_field_mappings_by_table_pair(self, db, seeded_mapping_with_field_mappings):
        m, v = seeded_mapping_with_field_mappings
        src_id, tgt_id = m.source_id, m.target_id

        result = _get_real_table_mappings(db, src_id, tgt_id)

        assert result == {"users": [{
            "target_table": "customers", "field_count": 3,
            "has_transformation": False,
        }]}

    def test_preserves_multiple_targets_and_transformation_signal(
        self, db, seeded_mapping_with_field_mappings,
    ):
        from app.models.mapping import FieldMapping

        m, version = seeded_mapping_with_field_mappings
        db.add(FieldMapping(
            mapping_id=m.id,
            version_id=version.id,
            target_table="opportunities",
            target_column="organization",
            sources=[{"table": "users", "column": "name", "type": "TEXT"}],
            transformation={"kind": "upper"},
            origin="manual",
        ))
        db.commit()

        result = _get_real_table_mappings(db, m.source_id, m.target_id)
        assert result["users"] == [
            {"target_table": "customers", "field_count": 3, "has_transformation": False},
            {"target_table": "opportunities", "field_count": 1, "has_transformation": True},
        ]

        graph = DiffService.generate_graph_data(
            SOURCE_SCHEMA,
            {**TARGET_SCHEMA, "opportunities": [{"name": "organization", "type": "TEXT"}]},
            DiffService.compare_schemas(
                SOURCE_SCHEMA,
                {**TARGET_SCHEMA, "opportunities": [{"name": "organization", "type": "TEXT"}]},
            ),
            real_mappings=result,
        )
        transformed = next(edge for edge in graph["edges"] if edge["target"] == "tgt_opportunities")
        assert transformed["edge_type"] == "transformation"
        assert transformed["label"] == "Transformed (1 field)"

    def test_returns_empty_when_no_published_mapping_exists(self, db, physical_sqlite_connections):
        src, tgt = physical_sqlite_connections
        assert _get_real_table_mappings(db, src.id, tgt.id) == {}

    def test_returns_empty_for_draft_mapping(self, db, physical_sqlite_connections):
        from app.models.mapping import Mapping

        src, tgt = physical_sqlite_connections
        db.add(Mapping(name="Draft", source_id=src.id, target_id=tgt.id, status="draft", created_by="test"))
        db.commit()

        assert _get_real_table_mappings(db, src.id, tgt.id) == {}
