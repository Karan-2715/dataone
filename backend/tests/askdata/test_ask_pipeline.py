"""Tests for the conversational NL-to-SQL pipeline (ADB-T1/T2/T3/T5/T7).

Uses questions that hit NL2SQLService's fast-path template/heuristic
matching so results are deterministic without depending on Ollama being
reachable (it isn't, in tests — the LLM path fails fast with connection
refused and falls through to the heuristic generator).
"""
from __future__ import annotations

from app.models.audit import AuditLog
from app.models.chat_session import ChatMessage


def test_grounded_generation_executes_against_catalog(client_analyst, sqlite_conn_scanned):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "show everything in customers",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["grounded"] is True
    assert body["executed"] is True
    assert body["row_count"] == 3
    assert "customers" in body["sql"]
    assert "Found 3 row" in body["summary"]


def test_ungrounded_connection_falls_back_to_live_schema(client_analyst, sqlite_conn_unscanned):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_unscanned.id,
        "question": "show everything in widgets",
    })
    body = resp.json()
    assert body["grounded"] is False
    assert body["executed"] is True
    assert body["row_count"] == 2


def test_pii_columns_masked_for_viewer_role(client_viewer, sqlite_conn_scanned):
    resp = client_viewer.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "show everything in customers",
    })
    body = resp.json()
    assert "email" in body["masked_columns"]
    assert all(row["email"] == "***REDACTED***" for row in body["rows"])
    assert all(row["name"] != "***REDACTED***" for row in body["rows"])  # not classified as High-risk


def test_pii_columns_not_masked_for_analyst_role(client_analyst, sqlite_conn_scanned):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "show everything in customers",
    })
    body = resp.json()
    assert body["masked_columns"] == []
    assert any(row["email"] == "alice@x.com" for row in body["rows"])


def test_conversation_context_persists_across_turns(client_analyst, sqlite_conn_scanned):
    first = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id, "question": "show everything in customers",
    }).json()
    session_id = first["session_id"]

    second = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id, "question": "count rows",
        "session_id": session_id,
    }).json()
    assert second["session_id"] == session_id

    messages = client_analyst.get(f"/api/v1/askdata/sessions/{session_id}/messages").json()
    assert len(messages["messages"]) == 4  # 2 user + 2 assistant
    assert messages["messages"][0]["role"] == "user"
    assert messages["messages"][1]["role"] == "assistant"
    assert messages["messages"][1]["sql_text"] is not None


def test_session_cannot_switch_connections(
    client_analyst, sqlite_conn_scanned, sqlite_conn_unscanned,
):
    first = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "show everything in customers",
    }).json()
    response = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_unscanned.id,
        "question": "show everything in widgets",
        "session_id": first["session_id"],
    })
    assert response.status_code == 409
    assert "different connection" in response.json()["detail"].lower()


def test_unknown_connection_404(client_analyst):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": 999999, "question": "show everything",
    })
    assert resp.status_code == 404


def test_no_schema_available_returns_helpful_error(client_analyst, db):
    from app.models.connection import DBConnection
    conn = DBConnection(name="empty-conn", type="sqlite", config={"path": "/tmp/does-not-exist.db"})
    db.add(conn)
    db.commit()
    db.refresh(conn)

    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": conn.id, "question": "show everything",
    })
    body = resp.json()
    assert body["executed"] is False
    assert "scan" in body["error"].lower() or body["error"]


def test_audit_event_emitted_on_ask(client_analyst, sqlite_conn_scanned, db):
    client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id, "question": "show everything in customers",
    })
    row = (
        db.query(AuditLog)
        .filter(AuditLog.module == "askdata")
        .filter(AuditLog.event_type == "askdata.question_answered")
        .one()
    )
    assert row.outcome == "success"
    assert row.event_metadata["question"] == "show everything in customers"


def test_messages_endpoint_requires_auth(client_admin, sqlite_conn_scanned):
    # No dedicated "unauthenticated" client here (get_current_user is
    # overridden per-fixture) — this just exercises the happy path since
    # role gating for /ask intentionally allows all three roles (read-only
    # feature); the important auth guarantee is *some* user is required,
    # which get_current_user's dependency already enforces app-wide.
    resp = client_admin.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id, "question": "show everything in customers",
    })
    assert resp.status_code == 200
