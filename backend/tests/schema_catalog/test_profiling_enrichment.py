"""Tests for profiling enrichment (agentic_dba_tasks #2): uniqueness ratio,
duplicate counting, and FK-candidate inference by PK value overlap."""
from __future__ import annotations

import sqlite3

import pytest

from app.connectors.sqlite import SQLiteConnector
from app.models.connection import DBConnection
from app.models.schema_catalog import CatalogColumn, CatalogTable, ColumnProfile
from app.services.profiling_enrichment import (
    compute_uniqueness_ratio,
    count_duplicate_values,
    infer_fk_candidates,
)


# ── Unit: pure computations ──────────────────────────────────────────────

def test_uniqueness_ratio_basic():
    assert compute_uniqueness_ratio(80, 100) == 0.8
    assert compute_uniqueness_ratio(100, 100) == 1.0


def test_uniqueness_ratio_unavailable_inputs():
    assert compute_uniqueness_ratio(None, 100) is None
    assert compute_uniqueness_ratio(10, None) is None
    assert compute_uniqueness_ratio(10, 0) is None


def test_uniqueness_ratio_caps_at_one():
    # distinct_count from a bounded scan can exceed a stale row_count.
    assert compute_uniqueness_ratio(120, 100) == 1.0


def test_uniqueness_ratio_uses_scanned_population_on_large_tables():
    # Regression (v3 bugs2 #1): distinct_count is measured over the first
    # `scanned_rows`, not the full table. A genuinely-unique key column on a
    # 1M-row table scanned at 100k must read ~1.0 (so the UNIQUE DQ rule
    # fires), NOT 0.1 (which silently disabled it).
    assert compute_uniqueness_ratio(100_000, 1_000_000, scanned_rows=100_000) == 1.0
    # Without the scan cap, the old math understates it and no rule can fire.
    assert compute_uniqueness_ratio(100_000, 1_000_000) == 0.1
    # A small table (below the cap) is unchanged: denominator stays row_count.
    assert compute_uniqueness_ratio(80, 100, scanned_rows=100_000) == 0.8


def test_count_duplicates():
    assert count_duplicate_values(["a", "b", "a", "c", "b", "a"]) == 2  # a and b repeat
    assert count_duplicate_values(["a", "b", "c"]) == 0
    assert count_duplicate_values([None, None, "x"]) == 0  # nulls aren't duplicates
    assert count_duplicate_values([]) == 0


# ── Integration: FK-candidate inference on a real seeded SQLite ─────────

@pytest.fixture()
def fk_fixture(db, tmp_path):
    """orders.customer_id values ⊂ customers.id (a real FK relationship),
    plus a noise column with no overlap."""
    path = str(tmp_path / "fk_test.db")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
    raw.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL)")
    raw.executemany("INSERT INTO customers (id, name) VALUES (?, ?)",
                    [(1, "a"), (2, "b"), (3, "c"), (4, "d")])
    raw.executemany("INSERT INTO orders (id, customer_id, amount) VALUES (?, ?, ?)",
                    [(1, 1, 10.0), (2, 1, 12.0), (3, 2, 9.0), (4, 3, 30.0)])
    raw.commit()
    raw.close()

    conn = DBConnection(name="fk-test", type="sqlite", config={"path": path})
    db.add(conn)
    db.commit()
    db.refresh(conn)

    for table_name, cols in {
        "customers": [("id", True), ("name", False)],
        "orders": [("id", True), ("customer_id", False), ("amount", False)],
    }.items():
        t = CatalogTable(connection_id=conn.id, table_name=table_name)
        db.add(t)
        db.flush()
        for pos, (col_name, is_pk) in enumerate(cols):
            db.add(CatalogColumn(table_id=t.id, column_name=col_name,
                                 data_type="INTEGER", nullable=not is_pk,
                                 is_primary_key=is_pk, ordinal_position=pos))
    db.commit()
    return conn


def test_fk_candidate_detected(db, fk_fixture):
    connector = SQLiteConnector(fk_fixture.config["path"])
    try:
        candidates = infer_fk_candidates(
            db, fk_fixture.id, connector, "orders", "customer_id",
            sample_values=[1, 1, 2, 3], db_type="sqlite",
        )
    finally:
        connector.close()
    assert candidates, "expected customers.id as an FK candidate"
    top = candidates[0]
    assert top["table"] == "customers"
    assert top["column"] == "id"
    assert top["overlap_ratio"] == 1.0


def test_no_fk_candidate_below_overlap_threshold(db, fk_fixture):
    connector = SQLiteConnector(fk_fixture.config["path"])
    try:
        candidates = infer_fk_candidates(
            db, fk_fixture.id, connector, "orders", "amount",
            sample_values=[10.0, 12.0, 9.0, 30.0], db_type="sqlite",
        )
    finally:
        connector.close()
    assert candidates == []


def test_no_candidates_for_empty_sample(db, fk_fixture):
    connector = SQLiteConnector(fk_fixture.config["path"])
    try:
        assert infer_fk_candidates(
            db, fk_fixture.id, connector, "orders", "customer_id",
            sample_values=[], db_type="sqlite",
        ) == []
    finally:
        connector.close()


# ── Integration: profile task persists the enrichment aggregates ────────

def test_profile_task_persists_enrichment(db, fk_fixture, monkeypatch):
    from app.core import database as db_module
    from app.tasks.schema_intel_tasks import profile_column_task

    class _NoClose:
        def __init__(self, s):
            self._s = s

        def __getattr__(self, name):
            if name == "close":
                return lambda: None
            return getattr(self._s, name)

    monkeypatch.setattr(db_module, "SessionLocal", lambda: _NoClose(db))

    col = (
        db.query(CatalogColumn)
        .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
        .filter(CatalogTable.connection_id == fk_fixture.id,
                CatalogTable.table_name == "orders",
                CatalogColumn.column_name == "customer_id")
        .one()
    )
    profile_column_task.run(
        connection_id=fk_fixture.id, table_name="orders",
        column_id=col.id, column_name="customer_id",
    )

    profile = db.query(ColumnProfile).filter(ColumnProfile.column_id == col.id).one()
    assert profile.row_count == 4
    assert profile.uniqueness_ratio == pytest.approx(0.75)  # 3 distinct / 4 rows
    assert profile.duplicate_count == 1                     # value 1 appears twice
    assert profile.fk_candidates
    assert profile.fk_candidates[0]["table"] == "customers"
    assert profile.fk_candidates[0]["overlap_ratio"] == 1.0
