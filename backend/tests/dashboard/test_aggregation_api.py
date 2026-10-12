"""Tests for GET /api/v1/dashboard/summary (dashboard_tasks #1)."""
from sqlalchemy import text

from app.models.connection import DBConnection
from app.models.mapping import FieldMapping, Mapping, MappingVersion
from app.models.schema_catalog import CatalogTable

EXPECTED_LABELS = {
    "Connected Sources", "Mappings", "Pipelines Running", "Pipelines Failed",
    "Queries", "Security Alerts", "Drift Events", "AI Autopilot Actions",
    "Total Tables", "Data Assets", "Mapped Assets %", "PII Columns",
    "Migration Readiness", "Critical Risks", "Data Quality Score",
    "Governance Compliance Score",
    "Total Schemas",
}


class TestDashboardSummary:
    def test_returns_200_with_envelope(self, client, seed_data):
        resp = client.get("/api/v1/dashboard/summary?range=7d")
        assert resp.status_code == 200
        data = resp.json()
        assert data["range"] == "7d"
        assert "generated_at" in data
        assert isinstance(data["kpis"], list)
        assert isinstance(data["feed"], list)
        assert data["available_views"] == ["combined", "executive", "operational"]

    def test_all_tiles_present(self, client, seed_data):
        data = client.get("/api/v1/dashboard/summary").json()
        assert {k["label"] for k in data["kpis"]} == EXPECTED_LABELS

    def test_default_range_is_7d(self, client, seed_data):
        assert client.get("/api/v1/dashboard/summary").json()["range"] == "7d"

    def test_invalid_range_returns_422(self, client):
        assert client.get("/api/v1/dashboard/summary?range=nope").status_code == 422

    def test_anonymous_returns_401(self, make_client):
        resp = make_client(None).get("/api/v1/dashboard/summary")
        assert resp.status_code == 401

    def test_seeded_counts(self, client, seed_data):
        tiles = {k["label"]: k for k in
                 client.get("/api/v1/dashboard/summary?range=7d").json()["kpis"]}
        assert tiles["Connected Sources"]["value"] == 2
        # Soft-deleted mapping excluded.
        assert tiles["Mappings"]["value"] == 1
        assert tiles["Pipelines Running"]["value"] == 1
        # 40-day-old failure excluded from the 7d window.
        assert tiles["Pipelines Failed"]["value"] == 1
        assert tiles["Queries"]["value"] == 2
        assert tiles["Security Alerts"]["value"] == 1
        assert tiles["Drift Events"]["value"] == 1
        assert tiles["AI Autopilot Actions"]["value"] == 1
        readiness = tiles["Migration Readiness"]
        assert readiness["value"] == 0
        assert readiness["subtitle"] == "No cataloged tables to assess"

    def test_catalog_kpis_exclude_deleted_connections_and_count_published_assets(
        self, client, db,
    ):
        active_src = DBConnection(name="ActiveSrc", type="sqlite", config={"path": "/tmp/a"})
        active_tgt = DBConnection(name="ActiveTgt", type="sqlite", config={"path": "/tmp/b"})
        deleted = DBConnection(
            name="Deleted", type="sqlite", config={"path": "/tmp/c"}, is_deleted=True,
        )
        db.add_all([active_src, active_tgt, deleted])
        db.flush()
        db.add_all([
            CatalogTable(connection_id=active_src.id, table_name="customers"),
            CatalogTable(connection_id=active_tgt.id, table_name="dim_customer"),
            CatalogTable(connection_id=deleted.id, table_name="ghost"),
        ])
        mapping = Mapping(
            name="Published", source_id=active_src.id, target_id=active_tgt.id,
            status="published", created_by="test",
        )
        draft = Mapping(
            name="Draft", source_id=active_src.id, target_id=active_tgt.id,
            status="draft", created_by="test",
        )
        db.add_all([mapping, draft])
        db.flush()
        version = MappingVersion(mapping_id=mapping.id, version_number=1, status="published")
        db.add(version)
        db.flush()
        mapping.current_version_id = version.id
        db.add(FieldMapping(
            mapping_id=mapping.id, version_id=version.id,
            target_table="dim_customer", target_column="id",
            sources=[{"table": "customers", "column": "id", "type": "INTEGER"}],
            transformation={"kind": "direct"}, audit={},
        ))
        db.commit()

        tiles = {k["label"]: k for k in client.get("/api/v1/dashboard/summary").json()["kpis"]}
        assert tiles["Total Tables"]["value"] == 2
        assert tiles["Total Schemas"]["value"] == 2
        assert tiles["Data Assets"]["value"] == 4
        assert tiles["Mapped Assets %"]["value"] == 100
        assert tiles["Mapped Assets %"]["subtitle"] == "2 of 2 cataloged tables published"

    def test_range_24h_excludes_older_rows(self, client, seed_data):
        tiles = {k["label"]: k for k in
                 client.get("/api/v1/dashboard/summary?range=24h").json()["kpis"]}
        # q2 is 2 days old.
        assert tiles["Queries"]["value"] == 1
        # Running is current state, not range-scoped.
        assert tiles["Pipelines Running"]["value"] == 1

    def test_empty_system_returns_zero_counts(self, client):
        data = client.get("/api/v1/dashboard/summary").json()
        assert {k["label"] for k in data["kpis"]} == EXPECTED_LABELS
        for kpi in data["kpis"]:
            assert kpi["status"] == "loaded"
            assert kpi["value"] == 0
        assert data["feed"] == []

    def test_each_tile_has_required_fields(self, client, seed_data):
        for kpi in client.get("/api/v1/dashboard/summary").json()["kpis"]:
            assert kpi["status"] in ("loaded", "error", "unavailable")
            assert isinstance(kpi["value"], int)
            assert kpi["link_url"]
            assert kpi["module"]

    def test_query_tile_targets_consolidated_workspace(self, client, seed_data):
        tiles = {k["label"]: k for k in client.get("/api/v1/dashboard/summary").json()["kpis"]}
        assert tiles["Queries"]["link_url"] == "/dashboard/query-workspace?mode=sql"

    def test_feed_reverse_chronological_and_range_scoped(self, client, seed_data):
        feed = client.get("/api/v1/dashboard/summary?range=30d").json()["feed"]
        assert len(feed) == 4  # the 40-day-old mapping_created event is excluded
        timestamps = [item["created_at"] for item in feed]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_feed_items_enriched_with_module_and_link(self, client, seed_data):
        feed = client.get("/api/v1/dashboard/summary").json()["feed"]
        by_type = {item["event_type"]: item for item in feed}
        connector = by_type["connector_created"]
        assert connector["module"] == "connectors"
        assert connector["link_url"] == "/dashboard/connectors"
        assert "Src" in connector["summary"]
        drift = by_type["schema_drift_detected"]
        assert drift["module"] == "schema_intel"
        assert drift["link_url"] == "/dashboard/schema"
        for item in feed:
            assert item["actor"]
            assert item["summary"]

    def test_broken_module_degrades_to_tile_not_500(self, client, engine, seed_data):
        """Per-module isolation (FR6): a missing table yields an
        'unavailable' tile and must not poison the other module queries."""
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE pipeline_runs"))
            conn.commit()
        resp = client.get("/api/v1/dashboard/summary")
        assert resp.status_code == 200
        tiles = {k["label"]: k for k in resp.json()["kpis"]}
        assert tiles["Pipelines Running"]["status"] == "unavailable"
        assert tiles["Pipelines Failed"]["status"] == "unavailable"
        # Modules queried after the failure still load (session recovered).
        assert tiles["Queries"]["status"] == "loaded"
        assert tiles["Drift Events"]["status"] == "loaded"
        assert len(resp.json()["feed"]) > 0
