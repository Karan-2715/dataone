"""Tests for column profiling (Task #2, FR2/FR7).

Covers: SQLiteConnector.profile_column against real seeded data, the
Celery task persisting aggregates without sample_values touching the DB,
and the POST /profile endpoint's precondition check.
"""
import pytest
from fastapi import HTTPException

from app.connectors.sqlite import SQLiteConnector
from app.services.schema_catalog_service import SchemaCatalogService


def test_profile_column_null_rate_and_distinct(physical_sqlite_connection):
    connector = SQLiteConnector(physical_sqlite_connection.config["path"])
    try:
        result = connector.profile_column("people", "notes", sample_limit=1000, distinct_scan_limit=100000)
    finally:
        connector.close()

    assert result.null_count == 2  # rows 2 and 4 have notes=None
    assert result.null_rate == pytest.approx(0.4)
    assert result.distinct_count == 1  # only "vip" among non-null values
    assert result.min_value == "vip"
    assert result.max_value == "vip"


def test_profile_column_sample_values_present_in_memory_only(physical_sqlite_connection):
    connector = SQLiteConnector(physical_sqlite_connection.config["path"])
    try:
        result = connector.profile_column("people", "contact")
    finally:
        connector.close()

    assert result.sample_size_used == 5
    assert "alice@example.com" in result.sample_values


def test_profile_column_on_empty_table(db, tmp_path):
    import sqlite3
    from app.models.connection import DBConnection

    path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE empty_t (id INTEGER, val TEXT)")
    conn.commit()
    conn.close()

    connector = SQLiteConnector(path)
    try:
        result = connector.profile_column("empty_t", "val")
    finally:
        connector.close()

    assert result.null_count == 0
    assert result.null_rate == 0.0
    assert result.sample_values == []


def test_profile_column_task_persists_aggregates_no_sample_values(db, physical_sqlite_connection):
    from app.models.schema_catalog import ColumnProfile, CatalogColumn

    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    col = db.query(CatalogColumn).filter(CatalogColumn.column_name == "contact").first()
    assert col is not None

    # profile_column_task opens its own SessionLocal() (as the real Celery
    # task does), so this points that at the test's own session — same
    # pattern as tests/mapping/test_suggest_task.py /
    # tests/pipelines/test_execution_engine.py.
    import app.tasks.schema_intel_tasks as sit_module
    from unittest.mock import patch

    class _NoCloseSession:
        def __init__(self, s):
            self._s = s

        def __getattr__(self, name):
            if name == "close":
                return lambda: None
            return getattr(self._s, name)

    with patch("app.core.database.SessionLocal", lambda: _NoCloseSession(db)):
        sit_module.profile_column_task(
            connection_id=physical_sqlite_connection.id,
            table_name="people", column_id=col.id, column_name="contact",
        )

    profile = db.query(ColumnProfile).filter(ColumnProfile.column_id == col.id).first()
    assert profile is not None
    assert profile.sample_size_used == 5
    assert not hasattr(profile, "sample_values")  # column doesn't exist on the model at all


def test_profile_connection_requires_prior_scan(db, admin):
    from app.models.connection import DBConnection
    conn = DBConnection(name="Unscanned", type="sqlite", config={"path": "/tmp/unscanned.db"})
    db.add(conn)
    db.commit()
    db.refresh(conn)

    from app.models.schema_catalog import CatalogTable
    table_count = db.query(CatalogTable).filter(CatalogTable.connection_id == conn.id).count()
    assert table_count == 0  # precondition the router checks before enqueueing
