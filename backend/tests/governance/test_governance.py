"""Tests for the Governance Intelligence Center (Enterprise v2, E08)."""
from __future__ import annotations

from app.models.schema_catalog import CatalogTable


class TestUpsertAndFetch:
    def test_anonymous_returns_401(self, make_client, connection):
        assert make_client(None).get(f"/api/v1/governance/{connection.id}").status_code == 401

    def test_viewer_forbidden_from_editing(self, make_client, viewer, connection):
        resp = make_client(viewer).put(f"/api/v1/governance/{connection.id}", json={"owner": "Alice"})
        assert resp.status_code == 403

    def test_score_is_visible_to_viewer(self, make_client, viewer):
        assert make_client(viewer).get("/api/v1/governance/score").status_code == 200

    def test_create_connection_level_record(self, client, connection):
        resp = client.put(f"/api/v1/governance/{connection.id}", json={
            "owner": "Data Platform Team", "steward": "Jane Doe",
            "classification": "Confidential", "retention_policy": "3 years",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["owner"] == "Data Platform Team"
        assert body["table_name"] is None
        assert body["column_name"] is None
        assert body["compliance_status"] == "not_assessed"
        assert body["updated_by"] == "admin@test.local"

    def test_upsert_updates_the_same_row_not_a_duplicate(self, client, connection):
        client.put(f"/api/v1/governance/{connection.id}", json={"owner": "First"})
        resp = client.put(f"/api/v1/governance/{connection.id}", json={"owner": "Second"})
        assert resp.json()["owner"] == "Second"

        rows = client.get(f"/api/v1/governance/{connection.id}").json()
        assert len(rows) == 1

    def test_table_and_column_level_records_are_distinct_rows(self, client, connection):
        client.put(f"/api/v1/governance/{connection.id}", json={"owner": "Conn Owner"})
        client.put(f"/api/v1/governance/{connection.id}", json={"table_name": "users", "owner": "Table Owner"})
        client.put(f"/api/v1/governance/{connection.id}", json={
            "table_name": "users", "column_name": "email", "owner": "Column Owner",
        })
        rows = client.get(f"/api/v1/governance/{connection.id}").json()
        assert len(rows) == 3

    def test_invalid_classification_returns_422(self, client, connection):
        resp = client.put(f"/api/v1/governance/{connection.id}", json={"classification": "Nonsense"})
        assert resp.status_code == 422

    def test_invalid_compliance_status_returns_422(self, client, connection):
        resp = client.put(f"/api/v1/governance/{connection.id}", json={"compliance_status": "made_up"})
        assert resp.status_code == 422


class TestEffectiveFallback:
    def test_column_falls_back_to_table_then_connection(self, db, connection):
        from app.services.governance_service import GovernanceService
        svc = GovernanceService(db)
        svc.upsert(connection.id, None, None, owner="Conn Owner", steward=None,
                  classification=None, retention_policy=None, compliance_status=None, updated_by="t")

        effective = svc.get_effective(connection.id, "orders", "amount")
        assert effective.owner == "Conn Owner"

        svc.upsert(connection.id, "orders", None, owner="Table Owner", steward=None,
                  classification=None, retention_policy=None, compliance_status=None, updated_by="t")
        effective = svc.get_effective(connection.id, "orders", "amount")
        assert effective.owner == "Table Owner"

        svc.upsert(connection.id, "orders", "amount", owner="Column Owner", steward=None,
                  classification=None, retention_policy=None, compliance_status=None, updated_by="t")
        effective = svc.get_effective(connection.id, "orders", "amount")
        assert effective.owner == "Column Owner"

    def test_no_record_anywhere_returns_none(self, db, connection):
        from app.services.governance_service import GovernanceService
        assert GovernanceService(db).get_effective(connection.id, "orders", "amount") is None


class TestGovernanceScore:
    def test_empty_catalog_is_honest_zero_not_fake(self, client):
        data = client.get("/api/v1/governance/score").json()
        assert data == {
            "score": 0, "table_count": 0,
            "owner_coverage": 0, "classification_coverage": 0, "retention_coverage": 0,
        }

    def test_score_reflects_real_coverage(self, client, db, connection):
        db.add_all([
            CatalogTable(connection_id=connection.id, table_name="users"),
            CatalogTable(connection_id=connection.id, table_name="orders"),
        ])
        db.commit()

        # Only "users" gets full governance; "orders" gets nothing —
        # connection-wide fallback doesn't exist either, so it's fully uncovered.
        client.put(f"/api/v1/governance/{connection.id}", json={
            "table_name": "users", "owner": "A", "classification": "PII", "retention_policy": "3y",
        })

        data = client.get("/api/v1/governance/score").json()
        assert data["table_count"] == 2
        assert data["owner_coverage"] == 50
        assert data["classification_coverage"] == 50
        assert data["retention_coverage"] == 50
        assert data["score"] == 50

    def test_connection_level_record_covers_every_table(self, client, db, connection):
        db.add_all([
            CatalogTable(connection_id=connection.id, table_name="users"),
            CatalogTable(connection_id=connection.id, table_name="orders"),
        ])
        db.commit()
        client.put(f"/api/v1/governance/{connection.id}", json={"owner": "Whole Connection Owner"})

        data = client.get("/api/v1/governance/score").json()
        assert data["owner_coverage"] == 100

    def test_score_can_be_scoped_to_one_connection(self, client, db, connection):
        from app.models.connection import DBConnection

        other = DBConnection(name="Other", type="sqlite", config={"path": "/tmp/other.db"})
        db.add(other)
        db.flush()
        db.add_all([
            CatalogTable(connection_id=connection.id, table_name="governed"),
            CatalogTable(connection_id=other.id, table_name="ungoverned"),
        ])
        db.commit()
        client.put(f"/api/v1/governance/{connection.id}", json={
            "owner": "A", "classification": "Public", "retention_policy": "1 year",
        })

        scoped = client.get(f"/api/v1/governance/score?connection_id={connection.id}").json()
        global_score = client.get("/api/v1/governance/score").json()
        assert scoped["score"] == 100
        assert scoped["table_count"] == 1
        assert global_score["score"] == 50
