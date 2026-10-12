"""Tests for CSV/JSON export (AUDIT-T6).

Covers the task's own "Verify" checklist:
  - CSV export with filters produces correct headers and rows
  - JSON export with filters produces valid NDJSON
  - streaming works end-to-end (regression: the export query runs in a
    dedicated session opened inside the generator, not the request-scoped
    Depends(get_db) session, which FastAPI closes before a StreamingResponse
    generator gets to iterate it — see _stream_export_rows's docstring)
  - export respects row limits
  - export with no results returns headers only (CSV) or empty (JSON)
"""
from __future__ import annotations

import csv
import io
import json

from app.core.config import settings
from app.services.audit_helper import emit_audit_event


def test_csv_export_has_headers_and_rows(client, db):
    emit_audit_event(db, event_type="connector.created", actor="a@x.com", module="connectors", summary="s1")
    emit_audit_event(db, event_type="query.executed", actor="b@x.com", module="query_studio", summary="s2")
    db.commit()

    resp = client.get("/api/v1/audit/export", params={"format": "csv"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "audit_export_" in resp.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][:3] == ["id", "event_type", "actor"]
    assert len(rows) == 3  # header + 2 events


def test_csv_export_applies_filters(client, db):
    emit_audit_event(db, event_type="connector.created", actor="a@x.com", module="connectors")
    emit_audit_event(db, event_type="query.executed", actor="b@x.com", module="query_studio")
    db.commit()

    resp = client.get("/api/v1/audit/export", params={"format": "csv", "module": "connectors"})
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert len(rows) == 2  # header + 1 matching event
    assert rows[1][1] == "connector.created"


def test_json_export_is_valid_ndjson(client, db):
    emit_audit_event(db, event_type="a.one", actor="a@x.com")
    emit_audit_event(db, event_type="a.two", actor="a@x.com")
    db.commit()

    resp = client.get("/api/v1/audit/export", params={"format": "json"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    lines = [l for l in resp.text.split("\n") if l]
    assert len(lines) == 2
    parsed = [json.loads(l) for l in lines]
    assert {p["event_type"] for p in parsed} == {"a.one", "a.two"}


def test_csv_export_with_no_results_is_headers_only(client, db):
    resp = client.get("/api/v1/audit/export", params={"format": "csv"})
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert len(rows) == 1  # header row only


def test_json_export_with_no_results_is_empty(client, db):
    resp = client.get("/api/v1/audit/export", params={"format": "json"})
    assert resp.text == ""


def test_export_respects_max_row_limit(client, db, monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_EXPORT_MAX_ROWS", 2)
    for i in range(5):
        emit_audit_event(db, event_type=f"e.{i}", actor="a@x.com")
    db.commit()

    resp = client.get("/api/v1/audit/export", params={"format": "json"})
    lines = [l for l in resp.text.split("\n") if l]
    assert len(lines) == 2


def test_export_streams_many_rows_without_session_errors(client, db):
    """Regression: querying through a session already closed by FastAPI's
    dependency teardown would surface as a mid-stream error here."""
    for i in range(50):
        emit_audit_event(db, event_type=f"bulk.{i}", actor="a@x.com")
    db.commit()

    resp = client.get("/api/v1/audit/export", params={"format": "json"})
    assert resp.status_code == 200
    lines = [l for l in resp.text.split("\n") if l]
    assert len(lines) == 50
