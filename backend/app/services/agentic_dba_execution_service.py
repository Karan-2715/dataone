"""Gated execution of approved SchemaDesignPlans (agentic_dba_tasks #7/#8/#9).

Thin wiring, not a new executor: every DDL statement executes through
query_execution_service.execute() — the SAME code path Query Studio's UI
hits with role=admin + confirm=true — so its role gate, timeout, and
single-statement classification all apply here for free.

Per-object apply tracking (task #9): each proposed table's statements run
as a unit with its own applied/failed/skipped status; a mid-plan failure
STOPS the run (a permission-class failure would likely affect later objects
too) and reports exactly what happened, never an opaque pass/fail.

Draft mapping auto-creation (task #8): only when the plan has a distinct
target connection — Schema Mapper's model requires source != target
(mapping_service.create_mapping 422s otherwise). Same-connection plans get
an honest note instead of a silently-broken mapping.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.connection import DBConnection
from app.models.schema_design_plan import SchemaDesignPlan
from app.services import query_execution_service
from app.services.audit_helper import emit_audit_event

logger = logging.getLogger(__name__)


def _get_plan_or_404(db: Session, plan_id: int) -> SchemaDesignPlan:
    plan = db.query(SchemaDesignPlan).filter(SchemaDesignPlan.id == plan_id).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


def reject_plan(db: Session, plan_id: int, *, actor: str) -> SchemaDesignPlan:
    plan = _get_plan_or_404(db, plan_id)
    if plan.status not in ("ready", "failed"):
        raise HTTPException(status_code=409,
                            detail=f"plan is {plan.status} — only ready/failed plans can be rejected")
    plan.status = "rejected"
    plan.decided_by = actor
    plan.decided_at = datetime.now(timezone.utc)
    emit_audit_event(
        db, event_type="agentic_dba.plan_rejected", actor=actor,
        module="agentic_dba", target_type="plan", target_id=plan.id,
        summary=f"plan {plan.id} rejected", outcome="success",
        metadata={"plan_id": plan.id},
    )
    db.commit()
    db.refresh(plan)
    return plan


def approve_and_execute_plan(db: Session, plan_id: int, *,
                             actor: str, role: str) -> SchemaDesignPlan:
    """Approve + apply a ready plan. Same role bar as Query Studio's own
    write gate (admin) — no weaker parallel permission check (task #7)."""
    # Lock the plan row for the duration of the status transition so two
    # concurrent admin approvals can't both pass the `status == ready` check
    # and double-apply the plan's DDL. (Harmless no-op on SQLite, enforced on
    # Postgres.)
    plan = (
        db.query(SchemaDesignPlan)
        .filter(SchemaDesignPlan.id == plan_id)
        .with_for_update()
        .first()
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    if role != "admin":
        raise HTTPException(status_code=403,
                            detail="applying a schema-design plan requires the admin role")
    if plan.status != "ready":
        raise HTTPException(status_code=409,
                            detail=f"plan is {plan.status} — only ready plans can be approved")

    target_id = plan.target_connection_id or plan.source_connection_id
    connection = (
        db.query(DBConnection)
        .filter(DBConnection.id == target_id,
                DBConnection.is_deleted == False)  # noqa: E712
        .first()
    )
    if connection is None:
        raise HTTPException(status_code=422, detail=f"target connection {target_id} not found or deleted")

    plan.status = "applying"
    plan.decided_by = actor
    plan.decided_at = datetime.now(timezone.utc)
    db.commit()

    results: List[Dict[str, Any]] = []
    stopped = False
    for obj in (plan.generated_ddl or []):
        table = obj.get("table")
        if stopped:
            results.append({"table": table, "mode": obj.get("mode"),
                            "status": "skipped", "error": None,
                            "statements_executed": 0})
            continue

        executed = 0
        error: str | None = None
        for sql in obj.get("statements", []):
            if sql.lstrip().startswith("--"):
                # Dialect-unsupported statement recorded as a comment at plan
                # time (e.g. SQLite ALTER COLUMN) — surfaced, never executed.
                continue
            logger.info("[agentic_dba] stage=apply_object table=%s", table)
            res = query_execution_service.execute(
                connection, sql, role=role, page=1, page_size=1, confirm=True,
            )
            if res.get("error") or not res.get("executed"):
                error = res.get("error") or "statement did not execute"
                break
            executed += 1

        status = "failed" if error else "applied"
        results.append({"table": table, "mode": obj.get("mode"),
                        "status": status, "error": error,
                        "statements_executed": executed})
        emit_audit_event(
            db,
            event_type=("agentic_dba.schema_object_created" if not error
                        else "agentic_dba.schema_object_failed"),
            actor=actor, module="agentic_dba",
            target_type="table", target_name=table,
            summary=f"{obj.get('mode')} {table}: {status}",
            outcome="success" if not error else "failure",
            metadata={"plan_id": plan.id, "table": table, "mode": obj.get("mode"),
                      "error": error, "statements_executed": executed},
        )
        if error:
            # Stop, don't continue blindly past a failure whose cause might
            # affect later objects (task #9 design decision).
            stopped = True

    applied = sum(1 for r in results if r["status"] == "applied")
    if applied == len(results) and results:
        plan.status = "applied"
    elif applied > 0:
        plan.status = "partially_applied"
    else:
        plan.status = "failed"
    plan.apply_results = results
    db.commit()

    if plan.status == "applied":
        _maybe_create_draft_mapping(db, plan, actor=actor)

    db.refresh(plan)
    return plan


def _maybe_create_draft_mapping(db: Session, plan: SchemaDesignPlan, *, actor: str) -> None:
    """Task #8: feed the existing Schema Mapper draft lifecycle — never a
    parallel one. Only possible with a distinct target connection."""
    transformations = [t for t in (plan.transformations or []) if t.get("transformation")]
    if not transformations:
        return
    if not plan.target_connection_id or plan.target_connection_id == plan.source_connection_id:
        notes = list(plan.confidence_notes or [])
        notes.append(
            "draft mapping not auto-created: Schema Mapper requires distinct source/target "
            "connections and this plan's targets were created in the source connection — "
            "create a mapping manually if data movement is needed"
        )
        plan.confidence_notes = notes
        db.commit()
        return

    from app.services.mapping_service import MappingService

    try:
        mapping = MappingService.create_mapping(
            db, source_id=plan.source_connection_id,
            target_id=plan.target_connection_id,
            name=f"agentic-dba-plan-{plan.id}", actor=actor,
        )
    except HTTPException as exc:
        logger.warning("[agentic_dba] draft mapping creation refused plan_id=%d: %s",
                       plan.id, exc.detail)
        return

    edges_added, edge_notes = 0, []
    for t in transformations:
        try:
            MappingService.add_edge(
                db, mapping.id,
                target={"table": t["target_table"], "column": t["target_column"],
                        "type": t.get("target_type"), "nullable": t.get("target_nullable")},
                sources=[{"table": s["table"], "column": s["column"]} for s in t["sources"]],
                transformation=t["transformation"],
                origin="agentic_dba", actor=actor,
            )
            edges_added += 1
        except HTTPException as exc:
            # Partial coverage is fine (task #8): the user completes these
            # manually in Schema Mapper's normal editor.
            edge_notes.append(
                f"edge {t['target_table']}.{t['target_column']} not auto-created: {exc.detail}")

    plan.created_mapping_id = mapping.id
    if edge_notes:
        plan.confidence_notes = list(plan.confidence_notes or []) + edge_notes
    emit_audit_event(
        db, event_type="agentic_dba.mapping_autocreated", actor=actor,
        module="agentic_dba", target_type="mapping", target_id=mapping.id,
        summary=f"draft mapping {mapping.id} auto-created from plan {plan.id} "
                f"({edges_added} edge(s))",
        outcome="success",
        metadata={"plan_id": plan.id, "mapping_id": mapping.id,
                  "edges_added": edges_added, "edges_skipped": len(edge_notes)},
    )
    db.commit()
