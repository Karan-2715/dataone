"""Tests for query execution + pagination + write gating (QS-T1/T2/T3)."""
from __future__ import annotations


def test_select_executes_and_paginates(client_analyst, sqlite_conn):
    resp = client_analyst.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id, "sql": "SELECT * FROM widgets ORDER BY id",
        "page": 1, "page_size": 2,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["statement_type"] == "select"
    assert body["executed"] is True
    assert body["row_count"] == 3
    assert len(body["rows"]) == 2
    assert body["has_more"] is True
    assert body["columns"] == ["id", "name", "qty"]


def test_select_second_page(client_analyst, sqlite_conn):
    resp = client_analyst.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id, "sql": "SELECT * FROM widgets ORDER BY id",
        "page": 2, "page_size": 2,
    })
    body = resp.json()
    assert len(body["rows"]) == 1
    assert body["has_more"] is False


def test_write_without_confirm_requires_confirmation_and_does_not_execute(client_admin, sqlite_conn, db):
    resp = client_admin.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id,
        "sql": "INSERT INTO widgets (name, qty) VALUES ('screw', 5)",
    })
    body = resp.json()
    assert body["statement_type"] == "insert"
    assert body["requires_confirmation"] is True
    assert body["executed"] is False

    count = client_admin.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id, "sql": "SELECT COUNT(*) AS n FROM widgets",
    }).json()
    assert count["rows"][0]["n"] == 3  # unchanged


def test_write_requires_admin_role(client_analyst, sqlite_conn):
    resp = client_analyst.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id,
        "sql": "INSERT INTO widgets (name, qty) VALUES ('screw', 5)",
        "confirm": True,
    })
    body = resp.json()
    assert body["requires_confirmation"] is True
    assert body["executed"] is False
    assert "admin role" in body["warnings"][-1]


def test_write_executes_and_commits_with_admin_and_confirm(client_admin, sqlite_conn):
    resp = client_admin.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id,
        "sql": "INSERT INTO widgets (name, qty) VALUES ('screw', 5)",
        "confirm": True,
    })
    body = resp.json()
    assert body["executed"] is True
    assert body["affected_rows"] == 1

    count = client_admin.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id, "sql": "SELECT COUNT(*) AS n FROM widgets",
    }).json()
    assert count["rows"][0]["n"] == 4  # persisted after connection close — proves the commit happened


def test_ddl_gated_same_as_dml(client_admin, sqlite_conn):
    resp = client_admin.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id,
        "sql": "ALTER TABLE widgets ADD COLUMN note TEXT",
        "confirm": True,
    })
    body = resp.json()
    assert body["statement_type"] == "ddl"
    assert body["executed"] is True


def test_multi_statement_rejected(client_admin, sqlite_conn):
    resp = client_admin.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id,
        "sql": "SELECT 1; SELECT 2",
    })
    body = resp.json()
    assert body["executed"] is False
    assert "one statement" in body["error"]


def test_unknown_statement_refused(client_analyst, sqlite_conn):
    resp = client_analyst.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id, "sql": "EXPLAIN QUERY PLAN nonsense",
    })
    body = resp.json()
    assert body["executed"] is False
    assert body["error"]


def test_viewer_role_forbidden(client_viewer, sqlite_conn):
    resp = client_viewer.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id, "sql": "SELECT * FROM widgets",
    })
    # 403 from require_role("admin", "analyst") — viewer never reaches the
    # connection lookup, so ownership scoping is irrelevant here.
    assert resp.status_code == 403


def test_unknown_connection_404(client_admin):
    resp = client_admin.post("/api/v1/query-studio/execute", json={
        "connection_id": 999999, "sql": "SELECT 1",
    })
    assert resp.status_code == 404
