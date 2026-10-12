"""Notify-out tests (aci_integration_tasks #5): per-type opt-in, dispatch on
pending-approval, and — most importantly — business-state isolation from
notification failures."""
from __future__ import annotations

import pytest

from app.models.autopilot import AutopilotRecommendation
from app.services.autopilot_service import AutopilotService
from app.services.notification_service import (
    dispatch_notify_out,
    is_notify_enabled,
    set_notify_enabled,
)


@pytest.fixture()
def dispatched(monkeypatch):
    """Capture Celery dispatches without a broker."""
    calls: list = []

    class _FakeTask:
        @staticmethod
        def delay(**kwargs):
            calls.append(kwargs)

    import app.tasks.aci_tasks as aci_tasks
    monkeypatch.setattr(aci_tasks, "notify_out_task", _FakeTask)
    return calls


def test_opt_in_defaults_to_disabled(db):
    assert is_notify_enabled(db, "autopilot:connector_health_check") is False


def test_set_and_read_flag(db):
    set_notify_enabled(db, "pipeline:run_failure", True, actor="admin@x")
    db.commit()
    assert is_notify_enabled(db, "pipeline:run_failure") is True


def test_dispatch_skipped_when_disabled(db, dispatched):
    sent = dispatch_notify_out(db, event_key="pipeline:run_failure",
                               title="t", body="b")
    assert sent is False
    assert dispatched == []


def test_dispatch_enqueued_when_enabled(db, dispatched):
    set_notify_enabled(db, "pipeline:run_failure", True, actor="admin@x")
    db.commit()
    sent = dispatch_notify_out(db, event_key="pipeline:run_failure",
                               title="run failed", link="http://x/pipelines")
    assert sent is True
    assert dispatched[0]["event_key"] == "pipeline:run_failure"
    assert dispatched[0]["link"] == "http://x/pipelines"


def test_recommendation_pending_approval_triggers_notify(db, dispatched):
    set_notify_enabled(db, "autopilot:connector_health_check", True, actor="admin@x")
    db.commit()
    rec, created = AutopilotService.upsert_recommendation(
        db, action_type="connector_health_check", subject="conn-1",
        payload={"connection_id": 1}, rationale={"summary": "conn down"},
        confidence=90.0, created_by="autopilot-engine",
    )
    db.commit()
    assert created is True
    assert len(dispatched) == 1
    assert dispatched[0]["event_key"] == "autopilot:connector_health_check"
    assert "pending approval" in dispatched[0]["title"]


def test_notify_disabled_recommendation_still_created_quietly(db, dispatched):
    rec, created = AutopilotService.upsert_recommendation(
        db, action_type="connector_health_check", subject="conn-2",
        payload={"connection_id": 2}, rationale={"summary": "x"},
        confidence=50.0, created_by="autopilot-engine",
    )
    db.commit()
    assert created is True
    assert dispatched == []


def test_notification_failure_never_breaks_the_business_write(db, monkeypatch):
    """THE load-bearing guarantee (task #5 risk note): a broker/dispatch
    failure must not fail or roll back the recommendation write."""
    set_notify_enabled(db, "autopilot:connector_health_check", True, actor="admin@x")
    db.commit()

    class _ExplodingTask:
        @staticmethod
        def delay(**kwargs):
            raise RuntimeError("broker down")

    import app.tasks.aci_tasks as aci_tasks
    monkeypatch.setattr(aci_tasks, "notify_out_task", _ExplodingTask)

    rec, created = AutopilotService.upsert_recommendation(
        db, action_type="connector_health_check", subject="conn-3",
        payload={"connection_id": 3}, rationale={"summary": "x"},
        confidence=70.0, created_by="autopilot-engine",
    )
    db.commit()
    assert created is True
    persisted = (
        db.query(AutopilotRecommendation)
        .filter(AutopilotRecommendation.subject == "conn-3")
        .one()
    )
    assert persisted.status == "pending"  # completely unaffected


def test_agentic_plan_ready_triggers_notify(db, dispatched, monkeypatch, tmp_path):
    import sqlite3
    from app.models.connection import DBConnection
    from app.models.schema_catalog import CatalogColumn, CatalogTable
    from app.services.agentic_dba_engine import create_plan, generate_plan
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENTIC_DBA_LLM_ENABLED", False)
    set_notify_enabled(db, "agentic_dba:schema_design_create", True, actor="admin@x")
    db.commit()

    path = str(tmp_path / "n.db")
    sqlite3.connect(path).close()
    conn = DBConnection(name="notify-src", type="sqlite", config={"path": path})
    db.add(conn)
    db.commit()
    db.refresh(conn)
    t = CatalogTable(connection_id=conn.id, table_name="things")
    db.add(t)
    db.flush()
    db.add(CatalogColumn(table_id=t.id, column_name="id", data_type="INTEGER",
                         nullable=False, is_primary_key=True, ordinal_position=0))
    db.commit()

    plan = generate_plan(db, create_plan(
        db, question="create a target table for things",
        connection_id=conn.id, session_id=None, actor="a@x").id)
    assert plan.status == "ready"
    assert len(dispatched) == 1
    assert dispatched[0]["event_key"] == "agentic_dba:schema_design_create"
    assert f"#{plan.id}" in dispatched[0]["title"]
