"""Unit tests for the Postgres/Oracle primary-key + foreign-key fix (Task #1).

Neither a real Postgres nor a real Oracle instance is available in this test
environment, so these tests mock the connection/cursor objects returned by
each connector's `connect()` and assert the queries this fix adds are wired
correctly -- i.e. that `primary_key` is derived from the new PK query instead
of the old hardcoded `False`, and that `foreign_keys` is populated from the
new FK query.
"""
from __future__ import annotations


class _FakeCursor:
    def __init__(self, rows, description=None):
        self._rows = rows
        self.description = description

    def execute(self, *args, **kwargs):
        pass

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConnection:
    def __init__(self, cursors):
        self._cursors = list(cursors)

    def cursor(self, *args, **kwargs):
        return self._cursors.pop(0)


def test_postgres_connector_detects_primary_key_and_foreign_keys(monkeypatch):
    from app.connectors.postgres import PostgresConnector

    connector = PostgresConnector(host="h", port=5432, dbname="d", user="u", password="p")

    columns_cursor = _FakeCursor([
        {"name": "id", "type": "integer", "nullable": False},
        {"name": "customer_id", "type": "integer", "nullable": False},
    ])
    pk_cursor = _FakeCursor([("id",)])
    fk_cursor = _FakeCursor([("customer_id", "customers", "id")])
    fake_conn = _FakeConnection([columns_cursor, pk_cursor, fk_cursor])
    monkeypatch.setattr(connector, "connect", lambda: fake_conn)

    schema = connector.get_table_schema("orders")
    by_name = {c["name"]: c for c in schema}

    assert by_name["id"]["primary_key"] is True
    assert by_name["customer_id"]["primary_key"] is False
    assert by_name["customer_id"]["foreign_keys"] == [
        {"references_table": "customers", "references_column": "id"},
    ]
    assert by_name["id"]["foreign_keys"] == []


def test_oracle_connector_real_branch_detects_primary_key_and_foreign_keys(monkeypatch):
    from app.connectors.oracle import OracleConnector

    connector = OracleConnector(
        host="dbhost", port=1521, service_name="orcl", user="scott", password="tiger",
    )
    assert connector._sim_mode is False

    columns_cursor = _FakeCursor(
        rows=[
            ("ID", "NUMBER", "N"),
            ("CUSTOMER_ID", "NUMBER", "N"),
        ],
        description=[("NAME",), ("TYPE",), ("NULLABLE",)],
    )
    pk_cursor = _FakeCursor([("ID",)])
    fk_cursor = _FakeCursor([("CUSTOMER_ID", "CUSTOMERS", "ID")])
    fake_conn = _FakeConnection([columns_cursor, pk_cursor, fk_cursor])
    monkeypatch.setattr(connector, "connect", lambda: fake_conn)

    schema = connector.get_table_schema("ORDERS")
    by_name = {c["name"]: c for c in schema}

    assert by_name["ID"]["primary_key"] is True
    assert by_name["CUSTOMER_ID"]["primary_key"] is False
    assert by_name["CUSTOMER_ID"]["foreign_keys"] == [
        {"references_table": "CUSTOMERS", "references_column": "ID"},
    ]


def test_oracle_connector_sim_mode_still_detects_primary_key_and_foreign_keys():
    from app.connectors.oracle import OracleConnector
    import sqlite3
    import tempfile
    import os

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        raw = sqlite3.connect(path)
        raw.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        raw.execute(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, "
            "customer_id INTEGER, FOREIGN KEY (customer_id) REFERENCES customers(id))"
        )
        raw.commit()
        raw.close()

        connector = OracleConnector(
            host="sim://local", port=0, service_name="x", user="u", password="p",
        )
        # Point the sim-mode file path at our prepared DB instead of the
        # default /shared/data location.
        import app.connectors.oracle as oracle_module  # noqa: F401

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        connector.conn = conn

        schema = connector.get_table_schema("orders")
        by_name = {c["name"]: c for c in schema}
        assert by_name["id"]["primary_key"] is True
        assert by_name["customer_id"]["foreign_keys"] == [
            {"references_table": "customers", "references_column": "id"},
        ]
    finally:
        os.remove(path)
