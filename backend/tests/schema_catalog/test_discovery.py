"""Catalog data model + discovery tests (Task #1, FR1/AC1).

Uses a real on-disk SQLite database as the "source" so scans exercise the
actual SQLiteConnector (PRAGMA table_info / PRAGMA foreign_key_list), not a
mock -- the bug this task fixes (hardcoded primary_key=False) only lives in
the Postgres/Oracle connectors, but the shared SchemaCatalogService logic
(upsert / full-replace / delete-stale-table) is connector-agnostic and best
exercised against a connector that actually enforces PK/FK constraints.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.models.connection import DBConnection
from app.models.schema_catalog import CatalogColumn, CatalogTable
from app.models.audit import AuditLog
from app.services.schema_catalog_service import SchemaCatalogService


def _make_sqlite_db(path: str, *, with_orders: bool = True) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        if with_orders:
            conn.execute(
                """
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY,
                    customer_id INTEGER NOT NULL,
                    FOREIGN KEY (customer_id) REFERENCES customers(id)
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def sqlite_connection(db, tmp_path):
    db_path = str(tmp_path / "catalog_source.db")
    _make_sqlite_db(db_path)
    conn = DBConnection(name="CatalogSource", type="sqlite", config={"path": db_path})
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn, db_path


def test_scan_connection_persists_tables_and_columns(db, admin, sqlite_connection):
    conn, _ = sqlite_connection
    result = SchemaCatalogService.scan_connection(db, conn.id, actor=admin.email)

    assert result["connection_id"] == conn.id
    assert result["tables_scanned"] == 2
    assert result["columns_scanned"] == 4  # customers: id,name / orders: id,customer_id

    tables = {t.table_name: t for t in db.query(CatalogTable).filter(CatalogTable.connection_id == conn.id).all()}
    assert set(tables.keys()) == {"customers", "orders"}

    customers_cols = {c.column_name: c for c in tables["customers"].columns}
    assert customers_cols["id"].is_primary_key is True
    assert customers_cols["name"].is_primary_key is False
    assert customers_cols["name"].nullable is False

    orders_cols = {c.column_name: c for c in tables["orders"].columns}
    assert orders_cols["customer_id"].is_primary_key is False
    fks = orders_cols["customer_id"].foreign_keys_rel
    assert len(fks) == 1
    assert fks[0].references_table == "customers"
    assert fks[0].references_column == "id"


def test_scan_connection_emits_audit_event(db, admin, sqlite_connection):
    conn, _ = sqlite_connection
    SchemaCatalogService.scan_connection(db, conn.id, actor=admin.email)

    events = db.query(AuditLog).filter(AuditLog.event_type == "schema_scanned").all()
    assert len(events) == 1
    assert events[0].connection_id == conn.id
    assert events[0].payload["tables_scanned"] == 2


def test_rescan_removes_stale_table(db, admin, sqlite_connection):
    conn, db_path = sqlite_connection
    SchemaCatalogService.scan_connection(db, conn.id, actor=admin.email)

    # Simulate a table being dropped at the source between scans.
    sconn = sqlite3.connect(db_path)
    sconn.execute("DROP TABLE orders")
    sconn.commit()
    sconn.close()

    result = SchemaCatalogService.scan_connection(db, conn.id, actor=admin.email)
    assert result["tables_scanned"] == 1

    remaining = db.query(CatalogTable).filter(CatalogTable.connection_id == conn.id).all()
    assert [t.table_name for t in remaining] == ["customers"]
    # Cascade: the deleted table's columns must be gone too.
    orphan_columns = (
        db.query(CatalogColumn)
        .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id, isouter=True)
        .filter(CatalogTable.id.is_(None))
        .all()
    )
    assert orphan_columns == []


def test_rescan_replaces_changed_columns(db, admin, sqlite_connection):
    conn, db_path = sqlite_connection
    SchemaCatalogService.scan_connection(db, conn.id, actor=admin.email)

    sconn = sqlite3.connect(db_path)
    sconn.execute("ALTER TABLE customers ADD COLUMN email TEXT")
    sconn.commit()
    sconn.close()

    SchemaCatalogService.scan_connection(db, conn.id, actor=admin.email)
    table = (
        db.query(CatalogTable)
        .filter(CatalogTable.connection_id == conn.id, CatalogTable.table_name == "customers")
        .first()
    )
    assert {c.column_name for c in table.columns} == {"id", "name", "email"}


def test_scan_connection_404s_on_missing_connection(db, admin):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        SchemaCatalogService.scan_connection(db, 999999, actor=admin.email)
    assert e.value.status_code == 404


def test_get_catalog_returns_persisted_tables(db, admin, sqlite_connection):
    conn, _ = sqlite_connection
    SchemaCatalogService.scan_connection(db, conn.id, actor=admin.email)

    tables = SchemaCatalogService.get_catalog(db, conn.id)
    assert {t.table_name for t in tables} == {"customers", "orders"}
