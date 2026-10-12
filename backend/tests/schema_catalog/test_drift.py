"""Drift detection completion tests (Task #6, FR6/AC3).

Covers:
- ``_check_single_connection_drift`` persists column-level ``DriftEvent`` rows
  (not just a table-count summary).
- ``POST /{id}/rescan`` triggers an on-demand rescans and returns the diff.
- ``GET /{id}/drift-history`` returns ``drift_event`` with column-level detail.
- Existing behaviour (audit emission, snapshot retention) is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import pytest
from sqlalchemy.orm import Session

from app.models.connection import DBConnection
from app.models.schema_snapshot import SchemaSnapshot
from app.models.drift_event import DriftEvent
from app.models.audit import AuditLog


def _compute_hash(schema: dict) -> str:
    """Compute the same SHA-256 hash that _check_single_connection_drift uses."""
    schema_str = json.dumps(schema, sort_keys=True, default=str)
    return hashlib.sha256(schema_str.encode()).hexdigest()


def _seed_connection(db: Session) -> DBConnection:
    conn = DBConnection(
        name="DriftTest",
        type="sqlite",
        config={"path": "/tmp/drifttest.db"},
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


def _seed_snapshot(
    db: Session,
    conn: DBConnection,
    schema: dict,
) -> SchemaSnapshot:
    schema_json = json.loads(json.dumps(schema, default=str))
    s = SchemaSnapshot(
        connection_id=conn.id,
        connection_name=conn.name,
        schema_hash=_compute_hash(schema),
        schema_json=schema_json,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _patch_schema_service(monkeypatch, schema: dict):
    """Replace ``SchemaService.get_full_schema`` to return *schema*."""
    from app.tasks import ai_tasks as tasks_mod
    monkeypatch.setattr(
        tasks_mod.SchemaService,
        "get_full_schema",
        staticmethod(lambda _conn: schema),
    )


# ── Unit tests for _check_single_connection_drift ──────────────────


def test_drift_event_persisted_on_change(db, monkeypatch):
    """When drift is detected, a DriftEvent with column-level detail is
    created (not just an AuditLog with a count summary)."""
    from app.tasks.ai_tasks import _check_single_connection_drift

    conn = _seed_connection(db)
    _seed_snapshot(db, conn, {
        "users": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
            {"name": "email", "type": "TEXT", "nullable": False, "primary_key": False},
        ],
    })
    # Live schema adds a table, adds a column, and changes a type
    _patch_schema_service(monkeypatch, {
        "users": [
            {"name": "id", "type": "BIGINT", "nullable": False, "primary_key": True},
            {"name": "email", "type": "TEXT", "nullable": False, "primary_key": False},
            {"name": "phone", "type": "TEXT", "nullable": True, "primary_key": False},
        ],
        "orders": [
            {"name": "order_id", "type": "INTEGER", "nullable": False, "primary_key": True},
        ],
    })

    result = _check_single_connection_drift(db, conn)
    db.commit()
    assert result["drift"] is True

    # ── DriftEvent exists ────────────────────────────────────────
    events = (
        db.query(DriftEvent)
        .filter(DriftEvent.connection_id == conn.id)
        .all()
    )
    assert len(events) == 1, "Expected exactly 1 DriftEvent"
    de = events[0]

    # Table-level: 'orders' is ADDED, no tables REMOVED
    assert "orders" in de.tables_added
    assert len(de.tables_removed) == 0

    # Column-level: 'phone' ADDED to 'users', 'id' type changed
    assert {"table": "users", "column": "phone"} in de.columns_added
    assert {"table": "users", "column": "id", "old_type": "INTEGER", "new_type": "BIGINT"} in de.type_changes
    assert len(de.columns_removed) == 0

    # ── AuditLog still emitted ───────────────────────────────────
    audits = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "schema_drift_detected")
        .all()
    )
    assert len(audits) == 1


def test_no_drift_no_event(db, monkeypatch):
    """When the schema hasn't changed, no DriftEvent is created and the
    result has ``drift=False``."""
    from app.tasks.ai_tasks import _check_single_connection_drift

    conn = _seed_connection(db)
    schema = {
        "users": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
        ],
    }
    _seed_snapshot(db, conn, schema)
    _patch_schema_service(monkeypatch, schema)

    result = _check_single_connection_drift(db, conn)
    db.commit()
    assert result["drift"] is False

    events = (
        db.query(DriftEvent)
        .filter(DriftEvent.connection_id == conn.id)
        .all()
    )
    assert len(events) == 0


def test_first_snapshot_no_event(db, monkeypatch):
    """On the first-ever scan for a connection, no DriftEvent is logged -
    only a baseline snapshot is stored."""
    from app.tasks.ai_tasks import _check_single_connection_drift

    conn = _seed_connection(db)
    _patch_schema_service(monkeypatch, {
        "users": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
        ],
    })

    result = _check_single_connection_drift(db, conn)
    db.commit()
    assert result["drift"] is False

    events = (
        db.query(DriftEvent)
        .filter(DriftEvent.connection_id == conn.id)
        .all()
    )
    assert len(events) == 0


def test_table_removed_triggers_drift(db, monkeypatch):
    """Removing a table from the schema creates a DriftEvent with
    ``tables_removed`` populated."""
    from app.tasks.ai_tasks import _check_single_connection_drift

    conn = _seed_connection(db)
    _seed_snapshot(db, conn, {
        "users": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
        ],
        "orders": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
        ],
    })
    _patch_schema_service(monkeypatch, {
        "users": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
        ],
    })

    result = _check_single_connection_drift(db, conn)
    db.commit()
    assert result["drift"] is True

    events = (
        db.query(DriftEvent)
        .filter(DriftEvent.connection_id == conn.id)
        .all()
    )
    assert len(events) == 1
    assert "orders" in events[0].tables_removed
    assert "users" not in events[0].tables_removed


# ── Integration: router function tests (no TestClient needed) ─────


def test_rescan_endpoint_returns_diff(db, monkeypatch):
    """POST /api/v1/schema/{id}/rescan triggers a scan and returns the
    diff with column-level detail when drift is detected."""
    from app.api.routers.schema import rescan_connection

    conn = _seed_connection(db)
    _seed_snapshot(db, conn, {
        "users": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
        ],
    })
    _patch_schema_service(monkeypatch, {
        "users": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
            {"name": "email", "type": "TEXT", "nullable": False, "primary_key": False},
        ],
    })

    result = rescan_connection(id=conn.id, db=db)
    assert result["drift"] is True
    assert result["connection"] == conn.name


def test_rescan_endpoint_returns_404_for_missing(db, monkeypatch):
    """POST /api/v1/schema/{id}/rescan returns 404 if the connection
    doesn't exist."""
    from app.api.routers.schema import rescan_connection
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        rescan_connection(id=99999, db=db)
    assert exc_info.value.status_code == 404


def test_drift_history_includes_drift_event(db, monkeypatch):
    """GET /api/v1/schema/{id}/drift-history includes a ``drift_event``
    key with column-level changes when drift has been recorded."""
    from app.tasks.ai_tasks import _check_single_connection_drift
    from app.api.routers.schema import get_drift_history

    conn = _seed_connection(db)
    _seed_snapshot(db, conn, {
        "users": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
        ],
    })
    _patch_schema_service(monkeypatch, {
        "users": [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
            {"name": "phone", "type": "TEXT", "nullable": True, "primary_key": False},
        ],
    })
    # Run drift check to persist the DriftEvent
    _check_single_connection_drift(db, conn)
    db.commit()

    result = get_drift_history(id=conn.id, db=db)
    assert result["connection"] == conn.name
    assert len(result["snapshots"]) == 2  # original + new

    # The second snapshot (most recent) should have a drift_event
    most_recent = result["snapshots"][0]
    de = most_recent.get("drift_event")
    assert de is not None, "Most recent snapshot should have a drift_event"
    assert len(de["columns_added"]) >= 1
    assert len(de["type_changes"]) == 0