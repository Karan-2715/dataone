"""Tests for saved queries (QS-T6), per-user history (QS-T6), and CSV export (QS-T5)."""
from __future__ import annotations

import csv
import io


def test_save_list_and_delete_query(client_analyst, sqlite_conn):
    create = client_analyst.post("/api/v1/query-studio/saved", json={
        "connection_id": sqlite_conn.id, "name": "All widgets", "sql_text": "SELECT * FROM widgets",
    })
    assert create.status_code == 200
    saved = create.json()
    assert saved["name"] == "All widgets"

    listed = client_analyst.get("/api/v1/query-studio/saved").json()
    assert len(listed) == 1
    assert listed[0]["id"] == saved["id"]

    delete = client_analyst.delete(f"/api/v1/query-studio/saved/{saved['id']}")
    assert delete.status_code == 204
    assert client_analyst.get("/api/v1/query-studio/saved").json() == []


def test_saved_queries_are_scoped_per_user(client_analyst, analyst, admin, switch_user, sqlite_conn):
    client_analyst.post("/api/v1/query-studio/saved", json={
        "connection_id": sqlite_conn.id, "name": "Analyst's query", "sql_text": "SELECT 1",
    })

    switch_user(admin)
    assert client_analyst.get("/api/v1/query-studio/saved").json() == []

    switch_user(analyst)
    assert len(client_analyst.get("/api/v1/query-studio/saved").json()) == 1


def test_cannot_delete_another_users_saved_query_but_admin_can(
    client_analyst, analyst2, admin, switch_user, sqlite_conn,
):
    created = client_analyst.post("/api/v1/query-studio/saved", json={
        "connection_id": sqlite_conn.id, "name": "mine", "sql_text": "SELECT 1",
    }).json()

    switch_user(analyst2)
    forbidden = client_analyst.delete(f"/api/v1/query-studio/saved/{created['id']}")
    assert forbidden.status_code == 403

    switch_user(admin)
    allowed = client_analyst.delete(f"/api/v1/query-studio/saved/{created['id']}")
    assert allowed.status_code == 204


def test_history_records_executions_for_current_user_only(
    client_analyst, analyst, admin, switch_user, sqlite_conn,
):
    client_analyst.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id, "sql": "SELECT * FROM widgets",
    })

    switch_user(admin)
    client_analyst.post("/api/v1/query-studio/execute", json={
        "connection_id": sqlite_conn.id, "sql": "SELECT 1",
    })
    admin_history = client_analyst.get("/api/v1/query-studio/history").json()
    assert admin_history["total"] == 1
    assert admin_history["history"][0]["sql"] == "SELECT 1"

    switch_user(analyst)
    analyst_history = client_analyst.get("/api/v1/query-studio/history").json()
    assert analyst_history["total"] == 1
    assert analyst_history["history"][0]["sql"] == "SELECT * FROM widgets"
    assert analyst_history["history"][0]["outcome"] == "success"


def test_csv_export_streams_full_result(client_analyst, sqlite_conn):
    resp = client_analyst.post("/api/v1/query-studio/export", json={
        "connection_id": sqlite_conn.id, "sql": "SELECT * FROM widgets ORDER BY id",
    })
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0] == ["id", "name", "qty"]
    assert len(rows) == 4  # header + 3 widgets


def test_export_rejects_write_statements(client_admin, sqlite_conn):
    resp = client_admin.post("/api/v1/query-studio/export", json={
        "connection_id": sqlite_conn.id, "sql": "DELETE FROM widgets",
    })
    assert resp.status_code == 400
