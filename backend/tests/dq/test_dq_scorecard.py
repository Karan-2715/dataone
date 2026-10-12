"""Tests for the Data Quality Observatory (Enterprise v2, E06)."""
from __future__ import annotations

from sqlalchemy import event

from app.models.schema_catalog import CatalogColumn, CatalogTable, ColumnProfile
from app.services.dq_service import _column_scores


def _seed_table(db, connection_id, table_name, columns):
    """columns: list of (name, null_rate, uniqueness_ratio, row_count, duplicate_count) | None for unprofiled."""
    table = CatalogTable(connection_id=connection_id, table_name=table_name)
    db.add(table)
    db.flush()
    for spec in columns:
        col = CatalogColumn(table_id=table.id, column_name=spec[0], data_type="TEXT")
        db.add(col)
        db.flush()
        if spec[1] is not None:
            _, null_rate, uniqueness_ratio, row_count, duplicate_count = spec
            db.add(ColumnProfile(
                column_id=col.id, null_rate=null_rate, uniqueness_ratio=uniqueness_ratio,
                row_count=row_count, duplicate_count=duplicate_count,
            ))
    db.commit()
    return table


class TestScorecardAPI:
    def test_consistency_is_unknown_without_row_count(self, db):
        profile = ColumnProfile(
            column_id=999, null_rate=0.0, uniqueness_ratio=1.0,
            row_count=None, duplicate_count=0,
        )
        assert _column_scores(profile)["consistency"] is None

    def test_anonymous_returns_401(self, make_client, connection):
        assert make_client(None).get(f"/api/v1/dq/scorecard/{connection.id}").status_code == 401

    def test_viewer_forbidden(self, make_client, viewer, connection):
        assert make_client(viewer).get(f"/api/v1/dq/scorecard/{connection.id}").status_code == 403

    def test_unknown_connection_returns_404(self, client):
        assert client.get("/api/v1/dq/scorecard/999999").status_code == 404

    def test_scorecard_computes_from_real_profiling(self, client, db, connection):
        _seed_table(db, connection.id, "users", [
            ("id", 0.0, 1.0, 100, 0),
            ("email", 0.02, 0.95, 100, 2),
        ])
        data = client.get(f"/api/v1/dq/scorecard/{connection.id}").json()
        assert data["connection_name"] == "DQ Conn"
        assert data["table_count"] == 1
        assert data["column_count"] == 2
        assert data["profiled_column_count"] == 2

        table = data["tables"][0]
        id_col = next(c for c in table["columns"] if c["column"] == "id")
        assert id_col["completeness"] == 100.0
        assert id_col["uniqueness"] == 100.0
        assert id_col["consistency"] == 100.0
        assert id_col["accuracy"] is None
        assert id_col["freshness"] is None

        email_col = next(c for c in table["columns"] if c["column"] == "email")
        assert email_col["completeness"] == 98.0
        assert email_col["consistency"] == 98.0  # 1 - 2/100

    def test_unprofiled_column_reports_null_scores_not_zero(self, client, db, connection):
        _seed_table(db, connection.id, "leads", [("company", None, None, None, None)])
        data = client.get(f"/api/v1/dq/scorecard/{connection.id}").json()
        col = data["tables"][0]["columns"][0]
        assert col["profiled"] is False
        assert col["completeness"] is None
        assert col["overall"] is None
        assert data["tables"][0]["overall"] is None
        assert data["overall"] is None

    def test_table_overall_averages_only_profiled_columns(self, client, db, connection):
        _seed_table(db, connection.id, "mixed", [
            ("a", 0.0, 1.0, 100, 0),      # overall 100
            ("b", None, None, None, None),  # unprofiled — excluded from the average
        ])
        data = client.get(f"/api/v1/dq/scorecard/{connection.id}").json()
        table = data["tables"][0]
        assert table["profiled_column_count"] == 1
        assert table["column_count"] == 2
        assert table["overall"] == 100.0


class TestSummaryAPI:
    def test_summary_uses_constant_query_count(self, db, engine):
        from app.models.connection import DBConnection
        from app.services.dq_service import DataQualityService

        connections = [
            DBConnection(name=f"C{i}", type="sqlite", config={"path": f"/tmp/c{i}.db"})
            for i in range(5)
        ]
        db.add_all(connections)
        db.flush()
        for index, connection in enumerate(connections):
            _seed_table(db, connection.id, f"t{index}", [("id", 0.0, 1.0, 10, 0)])

        statements = []
        listener = lambda *args: statements.append(args[2])
        event.listen(engine, "before_cursor_execute", listener)
        try:
            DataQualityService(db).get_all_scorecards_summary()
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 2  # active connections + all profile rows

    def test_summary_aggregates_across_connections(self, client, db):
        from app.models.connection import DBConnection

        c1 = DBConnection(name="C1", type="sqlite", config={"path": "/tmp/c1.db"})
        c2 = DBConnection(name="C2", type="sqlite", config={"path": "/tmp/c2.db"})
        db.add_all([c1, c2])
        db.flush()
        # duplicate_count=None on both isolates the assertion to
        # completeness/uniqueness — consistency stays None and is excluded
        # from the per-column average (see the None-vs-0 branch below).
        _seed_table(db, c1.id, "t1", [("a", 0.0, 1.0, 100, None)])
        _seed_table(db, c2.id, "t2", [("b", 0.5, 0.5, 100, None)])
        db.commit()

        data = client.get("/api/v1/dq/summary").json()
        assert data["connection_count"] == 2
        assert data["profiled_column_count"] == 2
        assert data["column_count"] == 2
        assert data["overall"] == 75.0  # avg of 100 and 50

    def test_summary_with_no_connections_is_honest_not_fake(self, client):
        data = client.get("/api/v1/dq/summary").json()
        assert data == {
            "overall": None, "connection_count": 0,
            "profiled_column_count": 0, "column_count": 0,
        }
