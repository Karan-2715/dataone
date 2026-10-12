"""Tests for GET /api/v1/risks and RiskAggregationService (Enterprise v2, E05)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.models.drift_event import DriftEvent
from app.models.mapping import Mapping, MappingVersion
from app.models.pipeline import Pipeline, PipelineRun
from app.models.schema_catalog import (
    CatalogColumn,
    CatalogTable,
    ColumnClassification,
)
from app.services.schema_service import SchemaService
from app.services.risk_service import RiskAggregationService


class TestRiskRegisterAPI:
    def test_anonymous_returns_401(self, make_client):
        assert make_client(None).get("/api/v1/risks").status_code == 401

    def test_viewer_forbidden(self, make_client, viewer):
        assert make_client(viewer).get("/api/v1/risks").status_code == 403

    def test_empty_register(self, client):
        resp = client.get("/api/v1/risks")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "total": 0, "findings": [], "partial": False, "partial_reason": None,
            "facets": {"by_category": {}, "by_severity": {}},
        }

    def test_invalid_category_returns_422(self, client):
        assert client.get("/api/v1/risks?category=not_a_category").status_code == 422


class TestMissingTargetTableAdapter:
    def test_register_cache_reuses_live_schema_results_across_filters(
        self, client, seeded_published_mapping, monkeypatch,
    ):
        original = SchemaService.get_full_schema
        calls = []

        def counted(connection):
            calls.append(connection.id)
            return original(connection)

        monkeypatch.setattr(SchemaService, "get_full_schema", staticmethod(counted))
        assert client.get("/api/v1/risks?category=missing_target_table").status_code == 200
        assert client.get("/api/v1/risks?severity=high").status_code == 200
        assert len(calls) == 2  # one source + one target, only on the first request

    def test_unmapped_table_and_type_mismatch_surface(self, client, seeded_published_mapping):
        data = client.get("/api/v1/risks?category=missing_target_table").json()
        titles = {f["title"] for f in data["findings"]}
        assert any("leads" in t for t in titles), titles
        assert any("users.age" in t for t in titles), titles
        # 'users' itself must NOT be flagged missing — it has a real
        # published mapping to 'customers'.
        assert not any(t.startswith("'users'") for t in titles)

    def test_missing_table_is_high_severity_type_mismatch_is_medium(self, client, seeded_published_mapping):
        data = client.get("/api/v1/risks?category=missing_target_table").json()
        by_title = {f["title"]: f for f in data["findings"]}
        missing = next(f for t, f in by_title.items() if "leads" in t)
        mismatch = next(f for t, f in by_title.items() if "users.age" in t)
        assert missing["severity"] == "high"
        assert mismatch["severity"] == "medium"

    def test_unreachable_connection_is_skipped_not_fatal(self, client, db, physical_sqlite_connections):
        src, tgt = physical_sqlite_connections
        # Point the target at a nonexistent file — the pair must be
        # skipped, not crash the whole register.
        tgt.config = {"path": "/nonexistent/does-not-exist.db"}
        m = Mapping(name="Broken", source_id=src.id, target_id=tgt.id,
                    status="draft", created_by="test")
        db.add(m)
        db.commit()

        resp = client.get("/api/v1/risks")
        assert resp.status_code == 200
        assert resp.json()["partial"] is False  # caught inside the adapter, not surfaced as a global failure


class TestPiiExposureAdapter:
    def test_pii_and_sensitive_columns_surface_with_severity(self, client, db, physical_sqlite_connections):
        src, _ = physical_sqlite_connections
        table = CatalogTable(connection_id=src.id, table_name="users")
        db.add(table)
        db.flush()
        col_email = CatalogColumn(table_id=table.id, column_name="email", data_type="TEXT")
        col_notes = CatalogColumn(table_id=table.id, column_name="notes", data_type="TEXT")
        db.add_all([col_email, col_notes])
        db.flush()
        db.add_all([
            ColumnClassification(column_id=col_email.id, label="PII", level="High",
                                  confidence=0.95, method="value_pattern"),
            ColumnClassification(column_id=col_notes.id, label="Sensitive", level="Medium",
                                  confidence=0.6, method="keyword"),
        ])
        db.commit()

        data = client.get("/api/v1/risks?category=pii_exposure").json()
        by_col = {f["column"]: f for f in data["findings"]}
        assert by_col["email"]["severity"] == "critical"
        assert by_col["notes"]["severity"] == "medium"
        assert by_col["email"]["connection_name"] == "RiskSrc"

    def test_public_columns_are_not_findings(self, client, db, physical_sqlite_connections):
        src, _ = physical_sqlite_connections
        table = CatalogTable(connection_id=src.id, table_name="users")
        db.add(table)
        db.flush()
        col = CatalogColumn(table_id=table.id, column_name="id", data_type="INTEGER")
        db.add(col)
        db.flush()
        db.add(ColumnClassification(column_id=col.id, label="Public", level="Low",
                                     confidence=1.0, method="keyword"))
        db.commit()

        data = client.get("/api/v1/risks?category=pii_exposure").json()
        assert data["total"] == 0


class TestSchemaDriftAdapter:
    def test_removed_table_and_type_change_surface(self, client, db, physical_sqlite_connections):
        src, _ = physical_sqlite_connections
        db.add(DriftEvent(
            connection_id=src.id, snapshot_id=1,
            tables_removed=["archive"],
            type_changes=[{"table": "users", "column": "age", "old_type": "INTEGER", "new_type": "TEXT"}],
            columns_removed=[],
        ))
        db.commit()

        data = client.get("/api/v1/risks?category=schema_drift").json()
        assert data["total"] == 2
        severities = {f["severity"] for f in data["findings"]}
        assert severities == {"high", "medium"}

    def test_only_latest_drift_event_counts(self, client, db, physical_sqlite_connections):
        src, _ = physical_sqlite_connections
        now = datetime.now(timezone.utc)
        db.add(DriftEvent(connection_id=src.id, snapshot_id=1,
                          tables_removed=["stale_finding"], detected_at=now - timedelta(days=5)))
        db.add(DriftEvent(connection_id=src.id, snapshot_id=2,
                          tables_removed=["current_finding"], detected_at=now))
        db.commit()

        data = client.get("/api/v1/risks?category=schema_drift").json()
        titles = " ".join(f["title"] for f in data["findings"])
        assert "current_finding" in titles
        assert "stale_finding" not in titles


class TestUnsupportedTransformationAdapter:
    def test_validation_eager_loads_edges_in_constant_queries(
        self, db, engine, physical_sqlite_connections,
    ):
        src, tgt = physical_sqlite_connections
        mappings = [
            Mapping(name=f"M{i}", source_id=src.id, target_id=tgt.id,
                    status="draft", created_by="test")
            for i in range(5)
        ]
        db.add_all(mappings)
        db.commit()
        statements = []
        listener = lambda *args: statements.append(args[2])
        event.listen(engine, "before_cursor_execute", listener)
        try:
            RiskAggregationService(db)._validation_findings()
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 3  # mappings, all edges, referenced connections

    def test_blocking_and_lossy_issues_surface(self, client, db, physical_sqlite_connections):
        src, tgt = physical_sqlite_connections
        m = Mapping(name="Bad Map", source_id=src.id, target_id=tgt.id,
                    status="draft", created_by="test")
        db.add(m)
        db.commit()

        from app.models.mapping import FieldMapping
        # Blocking: NOT NULL target, nullable source, no null-handling.
        db.add(FieldMapping(
            mapping_id=m.id, target_table="customers", target_column="full_name",
            target_type="TEXT", target_nullable=False,
            sources=[{"table": "users", "column": "name", "type": "TEXT", "nullable": True}],
            transformation={"kind": "direct"}, origin="manual",
        ))
        db.commit()

        data = client.get("/api/v1/risks?category=unsupported_transformation").json()
        assert data["total"] >= 1
        assert data["findings"][0]["severity"] == "high"
        assert data["findings"][0]["mapping_id"] == m.id


class TestBrokenDependencyAdapter:
    def test_drift_blocked_pipeline_run_surfaces(self, client, db, physical_sqlite_connections, seeded_published_mapping):
        src, tgt = physical_sqlite_connections
        mapping, version = seeded_published_mapping
        pipeline = Pipeline(name="Nightly ETL", source_connection_id=src.id,
                            target_connection_id=tgt.id, mapping_id=mapping.id,
                            mapping_version_id=version.id, created_by="test")
        db.add(pipeline)
        db.flush()
        db.add(PipelineRun(pipeline_id=pipeline.id, status="failed", trigger="manual",
                           started_at=datetime.now(timezone.utc),
                           error_message="Run blocked by schema drift on source connection"))
        db.commit()

        data = client.get("/api/v1/risks?category=broken_dependency").json()
        assert data["total"] == 1
        assert data["findings"][0]["title"] == "Pipeline 'Nightly ETL' blocked by schema drift"
        assert data["findings"][0]["severity"] == "high"

    def test_non_drift_failure_does_not_surface(self, client, db, physical_sqlite_connections, seeded_published_mapping):
        src, tgt = physical_sqlite_connections
        mapping, version = seeded_published_mapping
        pipeline = Pipeline(name="Other ETL", source_connection_id=src.id,
                            target_connection_id=tgt.id, mapping_id=mapping.id,
                            mapping_version_id=version.id, created_by="test")
        db.add(pipeline)
        db.flush()
        db.add(PipelineRun(pipeline_id=pipeline.id, status="failed", trigger="manual",
                           started_at=datetime.now(timezone.utc),
                           error_message="Connection timed out"))
        db.commit()

        data = client.get("/api/v1/risks?category=broken_dependency").json()
        assert data["total"] == 0


class TestFacetsAndFiltering:
    def test_facets_reflect_filtered_or_full_set(self, client, db, physical_sqlite_connections):
        src, _ = physical_sqlite_connections
        db.add(DriftEvent(connection_id=src.id, snapshot_id=1, tables_removed=["archive"]))
        db.commit()

        full = client.get("/api/v1/risks").json()
        assert full["facets"]["by_category"]["schema_drift"] == 1
        assert full["facets"]["by_severity"]["high"] >= 1

        filtered = client.get("/api/v1/risks?severity=high").json()
        assert all(f["severity"] == "high" for f in filtered["findings"])

    def test_findings_sorted_by_severity(self, client, db, physical_sqlite_connections):
        src, _ = physical_sqlite_connections
        db.add(DriftEvent(
            connection_id=src.id, snapshot_id=1,
            tables_removed=["archive"],  # high
            columns_removed=[{"table": "users", "column": "notes"}],  # medium
        ))
        db.commit()

        data = client.get("/api/v1/risks").json()
        ranks = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        severities = [ranks[f["severity"]] for f in data["findings"]]
        assert severities == sorted(severities)
