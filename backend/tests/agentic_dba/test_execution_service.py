"""Gated DDL execution tests (agentic_dba_tasks #7): execution genuinely
goes through query_execution_service, non-admin is rejected, audit events
land, and created tables really exist afterwards."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from app.models.audit import AuditLog
from app.services import query_execution_service
from app.services.agentic_dba_engine import create_plan, generate_plan
from app.services.agentic_dba_execution_service import (
    approve_and_execute_plan,
    reject_plan,
)

RETAIL_QUESTION = "create new target schemas for retail analytics based on profiling with data quality steps and target tables"


@pytest.fixture()
def ready_plan(db, retail_connection, admin):
    plan = create_plan(db, question=RETAIL_QUESTION, connection_id=retail_connection.id,
                       session_id=None, actor=admin.email)
    return generate_plan(db, plan.id)


def test_execution_goes_through_query_execution_service(db, ready_plan, admin, monkeypatch):
    calls = []
    real_execute = query_execution_service.execute

    def _spy(connection, sql, role, page, page_size, confirm):
        calls.append({"sql": sql, "role": role, "confirm": confirm})
        return real_execute(connection, sql, role=role, page=page,
                            page_size=page_size, confirm=confirm)

    monkeypatch.setattr(
        "app.services.agentic_dba_execution_service.query_execution_service.execute", _spy)

    plan = approve_and_execute_plan(db, ready_plan.id, actor=admin.email, role="admin")
    assert plan.status == "applied"
    assert calls, "expected DDL to flow through query_execution_service.execute"
    assert all(c["confirm"] is True and c["role"] == "admin" for c in calls)


def test_tables_actually_created(db, ready_plan, retail_connection, admin):
    plan = approve_and_execute_plan(db, ready_plan.id, actor=admin.email, role="admin")
    assert plan.status == "applied"
    raw = sqlite3.connect(retail_connection.config["path"])
    try:
        tables = {r[0] for r in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        raw.close()
    assert {"dim_customers", "dim_products", "fact_orders"} <= tables
    assert all(r["status"] == "applied" for r in plan.apply_results)


def test_non_admin_approval_rejected(db, ready_plan, analyst):
    with pytest.raises(HTTPException) as exc:
        approve_and_execute_plan(db, ready_plan.id, actor=analyst.email, role="analyst")
    assert exc.value.status_code == 403


def test_only_ready_plans_can_be_approved(db, ready_plan, admin):
    approve_and_execute_plan(db, ready_plan.id, actor=admin.email, role="admin")
    with pytest.raises(HTTPException) as exc:
        approve_and_execute_plan(db, ready_plan.id, actor=admin.email, role="admin")
    assert exc.value.status_code == 409


def test_audit_events_per_object(db, ready_plan, admin):
    approve_and_execute_plan(db, ready_plan.id, actor=admin.email, role="admin")
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "agentic_dba.schema_object_created")
        .all()
    )
    assert {r.event_metadata["table"] for r in rows} == {
        "dim_customers", "dim_products", "fact_orders"}


def test_reject_plan(db, ready_plan, admin):
    plan = reject_plan(db, ready_plan.id, actor=admin.email)
    assert plan.status == "rejected"
    assert plan.decided_by == admin.email
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "agentic_dba.plan_rejected")
        .one()
    )
    assert row.event_metadata["plan_id"] == plan.id


def test_registry_action_is_approval_only():
    """Design decision #1: schema_design_create must be structurally
    incapable of autonomous execution — same bar as migration_execute."""
    from app.services.autopilot_registry import ACTION_REGISTRY
    spec = ACTION_REGISTRY["schema_design_create"]
    assert spec.auto_capable is False
    assert spec.risk == "high"
    assert spec.reversible is False
