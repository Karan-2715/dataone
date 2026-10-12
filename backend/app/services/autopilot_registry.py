"""Autopilot action registry + server-side guardrails (ai_autopilot_tasks #4).

This module is the single source of truth for what Autopilot is *able* to do
(FR4) and what it must *never* do (FR5/AC3). Guardrails live here in the
service layer — the router and any policy configuration are irrelevant to
them:

- ``ACTION_REGISTRY`` is the allow-list. Anything not in it is refused
  (default-deny), which structurally subsumes every prohibited action.
- ``PROHIBITED_ACTION_TYPES`` additionally names the actions the TRD calls
  out (access-control / credential / security changes, irreversible deletes,
  mapping publish, raw DDL) so they fail with an explicit "prohibited
  regardless of policy configuration" error and are directly testable.
- ``auto_capable`` is a derived invariant: an action may run autonomously
  only if it is reversible AND low-risk (TRD FR4). Asserted at import time
  so a registry edit can't silently widen the autonomous surface.

Executor callables ground exclusively into already-shipped code paths;
imports are deferred into the callables to keep this module import-light
(it is imported by the router, services, and Celery tasks).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class ProhibitedActionError(Exception):
    """Raised for action types Autopilot must never perform (FR5)."""


class UnknownActionError(Exception):
    """Raised for action types not in the registry (default-deny)."""


class PayloadValidationError(Exception):
    """Raised when a recommendation payload fails registry validation."""


# Reserved key an executor may set in its result dict: a zero-arg callable
# the caller must invoke only AFTER its transaction commits (bugs/01). The
# key is popped before the result is persisted as execution_result.
DISPATCH_AFTER_COMMIT_KEY = "_dispatch_after_commit"


@dataclass(frozen=True)
class ActionSpec:
    action_type: str
    description: str
    risk: str  # low | medium | high
    reversible: bool
    reversibility_note: str
    auto_capable: bool
    required_payload_keys: FrozenSet[str]
    execute: Callable[[Session, Dict[str, Any], str], Dict[str, Any]]


# ── Executors (grounded in shipped code only) ─────────────────────────────


def _exec_connector_health_check(db: Session, payload: Dict[str, Any],
                                 actor: str) -> Dict[str, Any]:
    """Re-test a connection and persist its health status.

    Same core as ``run_health_check_for_connection`` (connector_tasks #5):
    SchemaService.test_connection owns the timeout and never raises.
    """
    from app.models.connection import DBConnection
    from app.services.connection_service import ConnectionService
    from app.services.schema_service import SchemaService

    conn = (
        db.query(DBConnection)
        .filter(DBConnection.id == payload["connection_id"],
                DBConnection.is_deleted == False)  # noqa: E712
        .first()
    )
    if not conn:
        raise ValueError(f"connection {payload['connection_id']} not found or deleted")
    result = SchemaService.test_connection(conn)
    if result.success:
        ConnectionService.update_health(db, conn.id, "healthy")
    elif result.reachable:
        ConnectionService.update_health(db, conn.id, "degraded", result.error_message)
    else:
        ConnectionService.update_health(db, conn.id, "down", result.error_message)
    return {
        "connection_id": conn.id,
        "success": result.success,
        "reachable": result.reachable,
        "error_code": result.error_code,
    }


def _exec_drift_rescan(db: Session, payload: Dict[str, Any],
                       actor: str) -> Dict[str, Any]:
    """Snapshot + drift-check one connection (same path as POST /schema/{id}/rescan)."""
    from app.models.connection import DBConnection
    from app.tasks.ai_tasks import _check_single_connection_drift

    conn = (
        db.query(DBConnection)
        .filter(DBConnection.id == payload["connection_id"],
                DBConnection.is_deleted == False)  # noqa: E712
        .first()
    )
    if not conn:
        raise ValueError(f"connection {payload['connection_id']} not found or deleted")
    result = _check_single_connection_drift(db, conn, actor=actor)
    if "error" in result:
        raise ValueError(f"rescan failed: {result['error']}")
    return {"connection_id": conn.id, "drift": result.get("drift", False)}


def _exec_mapping_suggestions_refresh(db: Session, payload: Dict[str, Any],
                                      actor: str) -> Dict[str, Any]:
    """Enqueue AI suggestion generation for a draft mapping.

    ``MappingService.request_suggestions`` asserts the mapping is a draft —
    a published mapping surfaces as a clean execution failure, not a crash.
    """
    from fastapi import HTTPException
    from app.services.mapping_service import MappingService

    try:
        task_id = MappingService.request_suggestions(
            db, payload["mapping_id"], actor=actor,
        )
    except HTTPException as exc:
        raise ValueError(f"suggestions refresh refused: {exc.detail}") from exc
    return {"mapping_id": payload["mapping_id"], "task_id": task_id}


def _exec_migration_execute(db: Session, payload: Dict[str, Any],
                            actor: str) -> Dict[str, Any]:
    """Start a legacy autopilot run in execute mode (approval-gated only —
    never auto; see auto_capable=False below).

    Transaction contract (bugs/01): executor callables NEVER commit the
    caller's session — ``execute_recommendation`` owns the boundary. The
    run row is flushed here and the Celery dispatch is returned under
    ``DISPATCH_AFTER_COMMIT_KEY`` so the caller can fire it strictly after
    the transaction lands (the worker writes FK'd AutopilotLog rows, so
    dispatching before commit races; committing here breaks atomicity).
    """
    import uuid
    from app.models.autopilot import AutopilotRun
    from app.models.connection import DBConnection
    from app.tasks.ai_tasks import run_autopilot_task

    source_id, target_id = payload["source_id"], payload["target_id"]
    for cid, label in ((source_id, "source"), (target_id, "target")):
        exists = (
            db.query(DBConnection)
            .filter(DBConnection.id == cid,
                    DBConnection.is_deleted == False)  # noqa: E712
            .first()
        )
        if not exists:
            raise ValueError(f"{label} connection {cid} not found or deleted")

    run_id = str(uuid.uuid4())
    db.add(AutopilotRun(
        id=run_id, source_id=source_id, target_id=target_id,
        mode="execute", model=payload.get("model", "llama3"), status="running",
    ))
    db.flush()

    def _dispatch() -> None:
        run_autopilot_task.delay(
            run_id=run_id, source_id=source_id,
            target_id=target_id, mode="execute",
        )

    return {"run_id": run_id, DISPATCH_AFTER_COMMIT_KEY: _dispatch}


def _exec_pipeline_schedule_disable(db: Session, payload: Dict[str, Any],
                                    actor: str) -> Dict[str, Any]:
    """Pause a pipeline's schedule after a terminal run failure.

    Approval-only (never auto): pausing future scheduled runs changes real
    business behavior even though the toggle itself is trivially
    reversible (a single re-enable call) — a human should weigh that
    trade-off, unlike the read-only connector_health_check probe.

    Stages the mutation directly (``db.flush()``, no commit) rather than
    calling the router-facing ``PipelineCRUD.toggle_schedule`` — that
    helper commits internally (and raises HTTPException), which would
    violate the executor transaction contract (bugs/01: executor callables
    NEVER commit the caller's session — ``execute_recommendation`` owns the
    boundary). Matches ``ConnectionService.update_health``'s "stages only —
    the caller owns the commit" contract, used by
    ``_exec_connector_health_check``. Raises a clean ValueError if the
    pipeline or its schedule doesn't exist, matching every other executor's
    fail-clean convention.
    """
    from app.models.pipeline import Pipeline

    pipeline_id = payload["pipeline_id"]
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if pipeline is None:
        raise ValueError(f"pipeline {pipeline_id} not found")
    if pipeline.schedule is None:
        raise ValueError(f"pipeline {pipeline_id} has no schedule to disable")
    pipeline.schedule.enabled = False
    db.flush()
    return {"pipeline_id": pipeline_id, "schedule_disabled": True}


def _audit_external_execution(db: Session, actor: str, action_type: str, *,
                              destination: str, result: Dict[str, Any]) -> None:
    """aci_integration_tasks #9: every executed external action is
    reconstructable from DataOne's own Audit Trail — app, destination,
    outcome — without consulting ACI's logs."""
    from app.services.audit_helper import emit_audit_event

    success = bool(result.get("success", True))
    emit_audit_event(
        db, event_type="aci.external_action_executed", actor=actor,
        module="aci_integration", target_type="external_action",
        target_name=destination,
        summary=f"{action_type} → {destination}",
        outcome="success" if success else "failure",
        metadata={"action_type": action_type, "destination": destination,
                  "success": success, "error": result.get("error")},
    )


def _exec_notify_slack_internal(db: Session, payload: Dict[str, Any],
                                actor: str) -> Dict[str, Any]:
    """Post to the ONE pre-configured internal Slack channel (aci tasks #3).

    Structurally enforces the fixed-destination rule: any channel in the
    payload is IGNORED — the destination comes only from admin-set Settings.
    That fixed, admin-controlled destination is what justifies this being
    the sole auto-capable external action.
    """
    from app.core.config import settings
    from app.services.aci_client_service import aci_client

    channel = settings.ACI_SLACK_INTERNAL_CHANNEL
    if not channel:
        raise ValueError(
            "notify_slack_internal is not configured "
            "(ACI_SLACK_INTERNAL_CHANNEL is unset)"
        )
    text = payload["title"]
    if payload.get("body"):
        text += f"\n{payload['body']}"
    if payload.get("link"):
        text += f"\n{payload['link']}"
    result = aci_client.execute_tool(
        "SLACK__CHAT_POST_MESSAGE", {"channel": channel, "text": text})
    return {"channel": channel, "success": bool(result.get("success", True))}


def _exec_external_ticket_create(db: Session, payload: Dict[str, Any],
                                 actor: str) -> Dict[str, Any]:
    """Create a ticket/issue in an external tracker — approval-only: it
    creates a persistent artifact in a system DataOne doesn't own."""
    from app.services.aci_client_service import aci_client

    tool_name = payload.get("tool_name") or "GITHUB__CREATE_ISSUE"
    result = aci_client.execute_tool(tool_name, {
        "title": payload["title"], "body": payload["body"],
        **(payload.get("tool_params") or {}),
    })
    _audit_external_execution(db, actor, "external_ticket_create",
                              destination=tool_name, result=result)
    return {"tool_name": tool_name, "success": bool(result.get("success", True)),
            "data": result.get("data")}


def _exec_external_message_send(db: Session, payload: Dict[str, Any],
                                actor: str) -> Dict[str, Any]:
    """Message an arbitrary (user-suppliable) channel/destination —
    approval-only regardless of the verb's low inherent risk, because the
    destination itself is part of the risk (aci tasks #3 crux rule)."""
    from app.services.aci_client_service import aci_client

    result = aci_client.execute_tool(
        "SLACK__CHAT_POST_MESSAGE",
        {"channel": payload["destination"], "text": payload["body"]})
    _audit_external_execution(db, actor, "external_message_send",
                              destination=payload["destination"], result=result)
    return {"destination": payload["destination"],
            "success": bool(result.get("success", True))}


def _exec_external_email_send(db: Session, payload: Dict[str, Any],
                              actor: str) -> Dict[str, Any]:
    """Send an email via ACI — highest blast radius (could reach an
    external, non-team recipient); approval-only, risk=high."""
    from app.services.aci_client_service import aci_client

    result = aci_client.execute_tool("GMAIL__SEND_EMAIL", {
        "recipient": payload["to"], "subject": payload["subject"],
        "body": payload["body"],
    })
    _audit_external_execution(db, actor, "external_email_send",
                              destination=payload["to"], result=result)
    return {"to": payload["to"], "success": bool(result.get("success", True))}


def _exec_schema_design_create(db: Session, payload: Dict[str, Any],
                               actor: str) -> Dict[str, Any]:
    """Apply an approved Agentic DBA schema-design plan (agentic_dba_tasks #7).

    Approval-only by construction (auto_capable=False, mirrors
    migration_execute): reaching this executor means a human approved the
    recommendation through the same admin-gated approval queue every other
    approval-only action uses. Execution itself still goes through Query
    Studio's existing write path inside the execution service — this
    executor adds no second execution engine.
    """
    from app.services.agentic_dba_execution_service import approve_and_execute_plan

    plan = approve_and_execute_plan(db, payload["plan_id"], actor=actor, role="admin")
    return {"plan_id": plan.id, "status": plan.status,
            "apply_results": plan.apply_results}


# ── Registry + prohibited set ─────────────────────────────────────────────


ACTION_REGISTRY: Dict[str, ActionSpec] = {
    spec.action_type: spec
    for spec in (
        ActionSpec(
            action_type="connector_health_check",
            description="Re-test a degraded/down connection and record its health status",
            risk="low",
            reversible=True,
            reversibility_note="Read-only probe; only updates the health-status fields, which the next scheduled check overwrites anyway.",
            auto_capable=True,
            required_payload_keys=frozenset({"connection_id"}),
            execute=_exec_connector_health_check,
        ),
        ActionSpec(
            action_type="drift_rescan",
            description="Snapshot a connection's schema and record drift against the previous snapshot",
            risk="low",
            reversible=True,
            reversibility_note="Additive: writes a new snapshot/drift-event row; no live schema or data is touched.",
            auto_capable=True,
            required_payload_keys=frozenset({"connection_id"}),
            execute=_exec_drift_rescan,
        ),
        ActionSpec(
            action_type="mapping_suggestions_refresh",
            description="Regenerate AI mapping suggestions for a draft mapping",
            risk="low",
            reversible=True,
            reversibility_note="Creates pending suggestions the user can reject; drafts only — publish state is never touched.",
            auto_capable=True,
            required_payload_keys=frozenset({"mapping_id"}),
            execute=_exec_mapping_suggestions_refresh,
        ),
        ActionSpec(
            action_type="migration_execute",
            description="Run the legacy autopilot migration (copies rows into the target connection)",
            risk="high",
            reversible=False,
            reversibility_note="NOT reversible: writes rows into the target database. Requires explicit human approval; never runs autonomously.",
            auto_capable=False,
            required_payload_keys=frozenset({"source_id", "target_id"}),
            execute=_exec_migration_execute,
        ),
        ActionSpec(
            action_type="schema_design_create",
            description="Apply an approved Agentic DBA schema-design plan (DDL via Query Studio's gated write path)",
            risk="high",
            reversible=False,
            reversibility_note="NOT reversible: creates/alters real schema objects in the target database. Approval-only, mirroring migration_execute — never runs autonomously (agentic_dba_tasks design decision #1).",
            auto_capable=False,
            required_payload_keys=frozenset({"plan_id"}),
            execute=_exec_schema_design_create,
        ),
        ActionSpec(
            action_type="pipeline_schedule_disable",
            description="Pause a repeatedly-failing pipeline's schedule until a human investigates",
            risk="medium",
            reversible=True,
            reversibility_note="Re-enabling the schedule is a single toggle; kept "
                               "approval-only because pausing future runs affects "
                               "real business behavior even though the action "
                               "itself is trivially reversible.",
            auto_capable=False,
            required_payload_keys=frozenset({"pipeline_id"}),
            execute=_exec_pipeline_schedule_disable,
        ),
        # ── External-system side effects (aci_integration_tasks #3) ───────
        # Same allow-list/risk/reversibility model — NOT a new authorization
        # dimension. Crux rule: an action whose destination is user- or
        # LLM-suppliable at request time is never auto_capable, regardless
        # of the verb's own risk — the destination is part of the risk.
        ActionSpec(
            action_type="notify_slack_internal",
            description="Post a notification to the ONE pre-configured internal Slack channel (via ACI)",
            risk="low",
            reversible=True,
            reversibility_note="A message to the fixed, admin-configured internal channel can be deleted/ignored; low blast radius because the destination is never user-suppliable (executor ignores any channel in the payload).",
            auto_capable=True,
            required_payload_keys=frozenset({"title"}),
            execute=_exec_notify_slack_internal,
        ),
        ActionSpec(
            action_type="external_message_send",
            description="Send a message to a user-specified channel/destination (via ACI)",
            risk="medium",
            reversible=True,
            reversibility_note="The message itself is deletable, but the destination is user-suppliable at request time — approval-only by the destination rule, never auto.",
            auto_capable=False,
            required_payload_keys=frozenset({"destination", "body"}),
            execute=_exec_external_message_send,
        ),
        ActionSpec(
            action_type="external_ticket_create",
            description="Create a ticket/issue in an external tracker (Jira/GitHub/Linear via ACI)",
            risk="medium",
            reversible=False,
            reversibility_note="Creates a persistent artifact in a system DataOne doesn't own — approval-only.",
            auto_capable=False,
            required_payload_keys=frozenset({"title", "body"}),
            execute=_exec_external_ticket_create,
        ),
        ActionSpec(
            action_type="external_email_send",
            description="Send an email to a specified recipient (via ACI)",
            risk="high",
            reversible=False,
            reversibility_note="NOT reversible: could reach an external, non-team recipient — the highest-blast-radius external action. Approval-only.",
            auto_capable=False,
            required_payload_keys=frozenset({"to", "subject", "body"}),
            execute=_exec_external_email_send,
        ),
    )
}

# TRD §2/§11: hard-blocked irrespective of any policy row (AC3). The
# default-deny registry already refuses these; naming them gives an explicit,
# testable "prohibited regardless of policy configuration" refusal.
PROHIBITED_ACTION_TYPES: FrozenSet[str] = frozenset({
    "connection_delete",
    "connection_hard_delete",
    "mapping_publish",
    "user_role_change",
    "credential_change",
    "security_setting_change",
    "ddl_execute",
})

# Import-time invariants: the autonomous surface can only contain reversible,
# low-risk actions, and nothing prohibited can ever be registered.
for _spec in ACTION_REGISTRY.values():
    assert not (_spec.auto_capable and not _spec.reversible), (
        f"{_spec.action_type}: auto_capable requires reversible"
    )
    assert not (_spec.auto_capable and _spec.risk != "low"), (
        f"{_spec.action_type}: auto_capable requires low risk"
    )
    assert _spec.action_type not in PROHIBITED_ACTION_TYPES, (
        f"{_spec.action_type}: prohibited actions can never be registered"
    )


def check_action_allowed(action_type: str) -> ActionSpec:
    """Server-side guardrail gate. Raises for prohibited/unknown types."""
    if action_type in PROHIBITED_ACTION_TYPES:
        raise ProhibitedActionError(
            f"action '{action_type}' is prohibited regardless of policy configuration"
        )
    spec = ACTION_REGISTRY.get(action_type)
    if spec is None:
        raise UnknownActionError(
            f"action '{action_type}' is not in the Autopilot allow-list (default deny)"
        )
    return spec


def validate_payload(spec: ActionSpec, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + normalize a payload against the spec (boundary validation)."""
    if not isinstance(payload, dict):
        raise PayloadValidationError("payload must be an object")
    missing = spec.required_payload_keys - payload.keys()
    if missing:
        raise PayloadValidationError(
            f"payload missing required keys: {sorted(missing)}"
        )
    normalized = dict(payload)
    for key in spec.required_payload_keys:
        if key.endswith("_id"):
            try:
                normalized[key] = int(normalized[key])
            except (TypeError, ValueError) as exc:
                raise PayloadValidationError(
                    f"payload key '{key}' must be an integer"
                ) from exc
    return normalized
