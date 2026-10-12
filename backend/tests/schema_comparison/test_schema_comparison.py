"""Tests for GET /api/v1/schema-comparison (Enterprise v2, E12)."""
from __future__ import annotations


class TestSchemaComparisonAPI:
    def test_anonymous_returns_401(self, make_client, comparison_connections):
        src, tgt = comparison_connections
        resp = make_client(None).get(f"/api/v1/schema-comparison?source_id={src.id}&target_id={tgt.id}")
        assert resp.status_code == 401

    def test_viewer_forbidden(self, make_client, viewer, comparison_connections):
        src, tgt = comparison_connections
        resp = make_client(viewer).get(f"/api/v1/schema-comparison?source_id={src.id}&target_id={tgt.id}")
        assert resp.status_code == 403

    def test_unknown_connection_returns_404(self, client, comparison_connections):
        src, _ = comparison_connections
        assert client.get(f"/api/v1/schema-comparison?source_id={src.id}&target_id=999999").status_code == 404

    def test_matched_table_reports_added_missing_type_and_constraint_diffs(
        self, client, comparison_connections,
    ):
        src, tgt = comparison_connections
        data = client.get(f"/api/v1/schema-comparison?source_id={src.id}&target_id={tgt.id}").json()
        assert data["source_name"] == "CmpSrc"
        assert data["target_name"] == "CmpTgt"

        users = next(t for t in data["tables"] if t["table"] == "users")
        assert users["status"] == "matched"
        assert users["added_columns"] == ["email"]
        assert users["missing_columns"] == ["legacy_flag"]
        # build-validation B-E12-08: source has {id,name,legacy_flag,age}
        # (4), target has {id,name,email,age} (4) — the real distinct
        # total across both sides is 5 (legacy_flag and email each only
        # exist on one side), not just the source's own count.
        assert users["column_count"] == 5

        type_change = next(c for c in users["changed_types"] if c["column"] == "age")
        assert type_change["source_type"] == "INTEGER"
        assert type_change["target_type"] == "TEXT"

        constraint_change = {c["column"]: c for c in users["changed_constraints"]}
        assert constraint_change["id"]["source_primary_key"] is True
        assert constraint_change["id"]["target_primary_key"] is False
        assert constraint_change["age"]["source_nullable"] is False
        assert constraint_change["age"]["target_nullable"] is True

    def test_source_only_and_target_only_tables_are_flagged(self, client, comparison_connections):
        src, tgt = comparison_connections
        data = client.get(f"/api/v1/schema-comparison?source_id={src.id}&target_id={tgt.id}").json()
        by_table = {t["table"]: t for t in data["tables"]}
        assert by_table["leads"]["status"] == "source_only"
        assert by_table["accounts"]["status"] == "target_only"
        # Whole-table-missing rows don't fabricate column-level diffs.
        assert by_table["leads"]["added_columns"] == []
        assert by_table["leads"]["changed_types"] == []

    def test_summary_counts_match_the_table_list(self, client, comparison_connections):
        src, tgt = comparison_connections
        data = client.get(f"/api/v1/schema-comparison?source_id={src.id}&target_id={tgt.id}").json()
        summary = data["summary"]
        assert summary["table_count"] == 3
        assert summary["matched_tables"] == 1
        assert summary["source_only_tables"] == 1
        assert summary["target_only_tables"] == 1
        assert summary["tables_with_changes"] == 1  # only 'users' has any diff

    def test_unreachable_connection_returns_502_not_500(self, client, db, comparison_connections):
        src, tgt = comparison_connections
        tgt.config = {"path": "/nonexistent/does-not-exist.db"}
        db.commit()
        resp = client.get(f"/api/v1/schema-comparison?source_id={src.id}&target_id={tgt.id}")
        assert resp.status_code == 502
