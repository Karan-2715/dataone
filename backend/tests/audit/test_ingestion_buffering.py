"""Tests for the ingestion API + durable buffering (AUDIT-T2).

Covers the task's own "Verify" checklist:
  - batch ingestion accepts valid events
  - a DB write failure falls back to the durable buffer instead of losing
    the event
  - backpressure: buffer full is surfaced (AuditBufferFullError / 503)
  - the flush task drains the buffer and persists once the DB recovers
"""
from __future__ import annotations

import pytest

from app.core import audit_buffer
from app.core.config import settings
from app.models.audit import AuditLog
from app.services import audit_helper
from app.services.audit_helper import (
    AuditBufferFullError,
    ingest_audit_event_durable,
)


def test_ingest_events_endpoint_accepts_valid_batch(client):
    resp = client.post(
        "/api/v1/audit/events",
        json={"events": [
            {"event_type": "connector.created", "actor": "a@x.com", "module": "connectors"},
            {"event_type": "query.executed", "actor": "b@x.com", "module": "query_studio"},
        ]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 0


def test_ingest_audit_event_durable_writes_directly_when_db_healthy(db, admin):
    correlation_id, buffered = ingest_audit_event_durable(
        db, event_type="pipeline.started", actor=admin.email, module="pipelines",
    )
    db.commit()

    assert buffered is False
    row = db.query(AuditLog).filter(AuditLog.correlation_id == correlation_id).one()
    assert row.event_type == "pipeline.started"


def test_falls_back_to_buffer_on_db_write_failure(db, admin, monkeypatch):
    def _always_fails(*args, **kwargs):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(audit_helper, "_write_audit_row", _always_fails)
    monkeypatch.setattr(audit_helper.time, "sleep", lambda *_: None)  # skip retry backoff

    correlation_id, buffered = ingest_audit_event_durable(
        db, event_type="query.executed", actor=admin.email, module="query_studio",
    )

    assert buffered is True
    assert audit_buffer.buffer_depth() == 1
    # nothing was persisted — the event only exists in the buffer
    assert db.query(AuditLog).filter(AuditLog.correlation_id == correlation_id).first() is None


def test_buffer_full_raises_and_signals_backpressure(db, admin, monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_BUFFER_MAX_SIZE", 1)
    monkeypatch.setattr(settings, "AUDIT_DB_WRITE_MAX_RETRIES", 0)

    def _always_fails(*args, **kwargs):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(audit_helper, "_write_audit_row", _always_fails)

    # First failure fills the 1-slot buffer.
    ingest_audit_event_durable(db, event_type="a.one", actor=admin.email)
    assert audit_buffer.buffer_depth() == 1

    # Second failure has nowhere to go.
    with pytest.raises(AuditBufferFullError):
        ingest_audit_event_durable(db, event_type="a.two", actor=admin.email)


def test_ingestion_endpoint_returns_503_when_fully_backpressured(client, monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_BUFFER_MAX_SIZE", 0)
    monkeypatch.setattr(settings, "AUDIT_DB_WRITE_MAX_RETRIES", 0)

    def _always_fails(*args, **kwargs):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(audit_helper, "_write_audit_row", _always_fails)

    resp = client.post(
        "/api/v1/audit/events",
        json={"events": [{"event_type": "connector.created", "actor": "a@x.com"}]},
    )

    assert resp.status_code == 503
    assert "Retry-After" in resp.headers


def test_flush_audit_buffer_task_persists_buffered_events(db):
    from app.tasks import audit_tasks

    class _NoCloseSession:
        """Proxy that hands the task the test session but ignores close()
        (same pattern as tests/mapping/test_suggest_task.py)."""

        def __init__(self, s):
            self._s = s

        def __getattr__(self, name):
            if name == "close":
                return lambda: None
            return getattr(self._s, name)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: _NoCloseSession(db))
    try:
        audit_buffer.buffer_event({"event_type": "buffered.event", "actor": "system"})
        assert audit_buffer.buffer_depth() == 1

        result = audit_tasks.flush_audit_buffer_task.run()

        assert result["flushed"] == 1
        assert audit_buffer.buffer_depth() == 0
        assert db.query(AuditLog).filter(AuditLog.event_type == "buffered.event").one()
    finally:
        monkeypatch.undo()
