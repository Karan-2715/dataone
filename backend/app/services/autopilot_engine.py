"""Autopilot recommendation engine (ai_autopilot_tasks #5, FR2).

Trigger evaluators are pure functions over persisted metadata/state —
rationale is deterministic and templated, never LLM-generated and never
derived from data *content* (TRD §10 prompt-injection mitigation, INDEX
design decision 2).

Trigger set v1 (grounded in tables that exist today):
  1. Connector health — connections currently degraded/down.
  2. Schema drift    — DriftEvents in the lookback window that affect
                       draft mappings.

Dedupe/supersede (INDEX decision 7): an open recommendation per
(action_type, subject) is refreshed in place, never duplicated; open
recommendations whose trigger has cleared are superseded.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.autopilot import AutopilotRecommendation
from app.models.connection import DBConnection
from app.models.drift_event import DriftEvent
from app.models.mapping import Mapping
from app.models.pipeline import Pipeline, PipelineRun
from app.services.audit_helper import record_audit
from app.services.autopilot_registry import (
    PayloadValidationError,
    ProhibitedActionError,
    UnknownActionError,
)
from app.services.autopilot_service import AutopilotService

logger = logging.getLogger(__name__)

ENGINE_ACTOR = "autopilot-engine"
# Action types this engine owns end-to-end (creates AND supersedes).
# migration_execute recs are human-created (legacy reroute) — never touched.
# notify_slack_internal/pipeline_schedule_disable are only owned for the
# pipeline-failure-escalation subject convention (see _supersede_cleared's
# "pipeline:" prefix guard) — other future evaluators may reuse those action
# types for unrelated subjects without being touched by that rule.
ENGINE_MANAGED_TYPES = (
    "connector_health_check", "mapping_suggestions_refresh",
    "notify_slack_internal", "pipeline_schedule_disable",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pipeline_id_from_subject(subject: str) -> Optional[int]:
    """Parse the pipeline-failure-escalation subject convention
    (`pipeline:{id}` / `pipeline:{id}:notify`) back to a pipeline id.

    Returns None for any other subject shape so unrelated
    notify_slack_internal/pipeline_schedule_disable recommendations a
    future evaluator might create for a different subject convention are
    never touched by the pipeline-specific supersede rule below.
    """
    if not subject.startswith("pipeline:"):
        return None
    parts = subject.split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


class AutopilotEngine:

    # ── Evaluators ────────────────────────────────────────────

    @staticmethod
    def _evaluate_connector_health(db: Session) -> List[Dict[str, Any]]:
        drafts: List[Dict[str, Any]] = []
        unhealthy = (
            db.query(DBConnection)
            .filter(
                DBConnection.is_deleted == False,  # noqa: E712
                DBConnection.health_status.in_(("degraded", "down")),
            )
            .all()
        )
        for conn in unhealthy:
            evidence = [
                f"health_status={conn.health_status}",
                f"last_tested_at={conn.last_tested_at}",
            ]
            if conn.last_test_error:
                evidence.append(f"last_test_error={conn.last_test_error[:200]}")
            dependents = [
                m.name for m in (
                    db.query(Mapping)
                    .filter(
                        Mapping.deleted_at.is_(None),
                        (Mapping.source_id == conn.id) | (Mapping.target_id == conn.id),
                    )
                    .limit(5)
                    .all()
                )
            ]
            if dependents:
                evidence.append(f"used by mappings: {', '.join(dependents)}")
            drafts.append({
                "action_type": "connector_health_check",
                "subject": f"connection:{conn.id}",
                "payload": {"connection_id": conn.id},
                "confidence": 90.0,  # mechanical re-test; near-certain applicability
                "rationale": {
                    "summary": (
                        f"Connection '{conn.name}' is {conn.health_status}; "
                        "re-test to confirm recovery or persistent failure."
                    ),
                    "evidence": evidence,
                    "trigger": {
                        "kind": "connector_health",
                        "connection_id": conn.id,
                        "health_status": conn.health_status,
                    },
                },
            })
        return drafts

    @staticmethod
    def _evaluate_schema_drift(db: Session) -> List[Dict[str, Any]]:
        drafts: List[Dict[str, Any]] = []
        since = _now() - timedelta(hours=settings.AUTOPILOT_DRIFT_LOOKBACK_HOURS)
        # bugs/04: newest event per connection is computed in SQL — loading
        # every event in the window into Python doesn't scale (the beat runs
        # this every 2 minutes). max(id) is the newest: ids are insert-ordered.
        latest_ids = (
            db.query(func.max(DriftEvent.id).label("max_id"))
            .filter(DriftEvent.detected_at >= since)
            .group_by(DriftEvent.connection_id)
            .subquery()
        )
        events = (
            db.query(DriftEvent)
            .join(latest_ids, DriftEvent.id == latest_ids.c.max_id)
            .all()
        )
        latest_by_conn: Dict[int, DriftEvent] = {
            ev.connection_id: ev for ev in events
        }

        for conn_id, ev in latest_by_conn.items():
            affected = (
                db.query(Mapping)
                .filter(
                    Mapping.status == "draft",
                    Mapping.deleted_at.is_(None),
                    (Mapping.source_id == conn_id) | (Mapping.target_id == conn_id),
                )
                .all()
            )
            if not affected:
                continue
            added = len(ev.tables_added or []) + len(ev.columns_added or [])
            removed = len(ev.tables_removed or []) + len(ev.columns_removed or [])
            retyped = len(ev.type_changes or [])
            # Additions mean new unmapped surface — suggestions directly help;
            # removals/retypes still warrant a refresh but less certainly.
            confidence = 80.0 if added else 65.0
            conn = db.query(DBConnection).filter(DBConnection.id == conn_id).first()
            conn_name = conn.name if conn else f"#{conn_id}"
            for m in affected:
                drafts.append({
                    "action_type": "mapping_suggestions_refresh",
                    "subject": f"mapping:{m.id}",
                    "payload": {"mapping_id": m.id},
                    "confidence": confidence,
                    "rationale": {
                        "summary": (
                            f"Schema drift on connection '{conn_name}' "
                            f"(+{added} added, -{removed} removed, "
                            f"{retyped} type changes); refresh AI suggestions "
                            f"for draft mapping '{m.name}' so the changed "
                            "columns get mapped."
                        ),
                        "evidence": [
                            f"drift_event_id={ev.id}",
                            f"detected_at={ev.detected_at}",
                            f"tables_added={ev.tables_added}",
                            f"columns_added={len(ev.columns_added or [])}",
                            f"columns_removed={len(ev.columns_removed or [])}",
                            f"type_changes={retyped}",
                        ],
                        "trigger": {
                            "kind": "schema_drift",
                            "drift_event_id": ev.id,
                            "connection_id": conn_id,
                        },
                    },
                })
        return drafts

    @staticmethod
    def _evaluate_new_draft_mappings(db: Session) -> List[Dict[str, Any]]:
        """Warm AI suggestions for freshly created mappings (mapping-suggestions
        precompute design, 2026-08-01): a draft mapping whose suggestions have
        never been requested — manually or automatically — is exactly the set
        with ``pending_suggestion_task_id IS NULL``, no separate timestamp or
        lookback window needed.

        Same action_type/subject convention as ``_evaluate_schema_drift`` so
        the two dedupe into one recommendation when a mapping is both new and
        already drift-affected in the same sweep; see
        ``_merge_drafts_by_subject`` in ``evaluate_all`` for how that merge is
        resolved deterministically rather than by evaluator call order.
        """
        drafts: List[Dict[str, Any]] = []
        mappings = (
            db.query(Mapping)
            .filter(
                Mapping.status == "draft",
                Mapping.deleted_at.is_(None),
                Mapping.pending_suggestion_task_id.is_(None),
            )
            .all()
        )
        for m in mappings:
            drafts.append({
                "action_type": "mapping_suggestions_refresh",
                "subject": f"mapping:{m.id}",
                "payload": {"mapping_id": m.id},
                "confidence": 85.0,
                "rationale": {
                    "summary": (
                        f"Mapping '{m.name}' was just created with no AI "
                        "suggestions yet — generate an initial pass so the "
                        "mapper opens pre-populated."
                    ),
                    "evidence": [
                        f"mapping_id={m.id}",
                        f"created_at={m.created_at}",
                    ],
                    "trigger": {"kind": "mapping_created", "mapping_id": m.id},
                },
            })
        return drafts

    @staticmethod
    def _evaluate_pipeline_failures(db: Session) -> List[Dict[str, Any]]:
        """Escalate pipelines whose latest run is a terminal failure into
        governed recommendations (pipeline-failure-autopilot-escalation
        design, §4-5): "retries_exhausted" is a Celery return value, not a
        persisted column, so a run resting at status='failed' is by
        construction terminal — retries were either exhausted or the error
        was classified non-retryable and never retried at all.
        """
        drafts: List[Dict[str, Any]] = []
        # Latest run per pipeline computed in SQL (same "bugs/04" shape as
        # _evaluate_schema_drift's latest-drift-per-connection query above):
        # loading every run into Python doesn't scale on a 2-minute beat.
        # max(id) is the newest: ids are insert-ordered.
        latest_ids = (
            db.query(func.max(PipelineRun.id).label("max_id"))
            .group_by(PipelineRun.pipeline_id)
            .subquery()
        )
        latest_failed_runs = (
            db.query(PipelineRun)
            .join(latest_ids, PipelineRun.id == latest_ids.c.max_id)
            .filter(PipelineRun.status == "failed")
            .all()
        )
        if not latest_failed_runs:
            return drafts

        pipelines = {
            p.id: p
            for p in (
                db.query(Pipeline)
                .options(joinedload(Pipeline.schedule), joinedload(Pipeline.source_connection))
                .filter(Pipeline.id.in_([r.pipeline_id for r in latest_failed_runs]))
                .all()
            )
        }

        for run in latest_failed_runs:
            pipeline = pipelines.get(run.pipeline_id)
            if pipeline is None:
                continue  # stale run row (pipeline deleted) — nothing to escalate
            error_message = (run.error_message or "")[:300]
            evidence = [
                f"run_id={run.id}",
                f"pipeline_name={pipeline.name}",
                f"retry_count={run.retry_count}",
            ]
            if run.error_message:
                evidence.append(f"error_message={error_message}")
            trigger = {
                "kind": "pipeline_failure",
                "pipeline_id": pipeline.id,
                "run_id": run.id,
            }

            # 1) Re-test the source connection — same subject convention as
            # _evaluate_connector_health so the two dedupe into one rec.
            # Skip when the connection's cached health_status is already
            # "healthy": _supersede_cleared's existing rule for this action
            # type closes any open connector_health_check the instant
            # health_status == "healthy" (correct for its original producer,
            # _evaluate_connector_health, which only ever drafts when
            # already degraded/down). Drafting here regardless of the
            # cached status would create-then-instantly-supersede a rec
            # every sweep for as long as the pipeline keeps failing —
            # unbounded audit/notify churn with zero value, since a
            # connection already believed healthy gets no marginal benefit
            # from this proactive re-test.
            conn = pipeline.source_connection
            if conn is not None and conn.health_status != "healthy":
                drafts.append({
                    "action_type": "connector_health_check",
                    "subject": f"connection:{pipeline.source_connection_id}",
                    "payload": {"connection_id": pipeline.source_connection_id},
                    "confidence": 85.0,
                    "rationale": {
                        "summary": (
                            f"Pipeline '{pipeline.name}' latest run #{run.id} "
                            "failed; re-test its source connection to confirm "
                            "the failure isn't a connectivity issue."
                        ),
                        "evidence": evidence,
                        "trigger": trigger,
                    },
                })

            # 2) Notify — routes through the governed Autopilot audit/rate
            # -limit path (not the fire-and-forget dispatch_notify_out
            # helper already fired from run_pipeline_task).
            drafts.append({
                "action_type": "notify_slack_internal",
                "subject": f"pipeline:{pipeline.id}:notify",
                "payload": {
                    "title": f"Pipeline '{pipeline.name}' has failed",
                    "body": error_message,
                    "link": f"{settings.DATAONE_BASE_URL}/dashboard/pipelines",
                },
                "confidence": 90.0,
                "rationale": {
                    "summary": (
                        f"Pipeline '{pipeline.name}' latest run #{run.id} "
                        "ended in a terminal failure; notify so a human can "
                        "investigate."
                    ),
                    "evidence": evidence,
                    "trigger": trigger,
                },
            })

            # 3) Propose pausing the schedule — only when there is one to
            # pause and it isn't already disabled (no point proposing a
            # no-op); approval-only judgment call, hence lower confidence.
            if pipeline.schedule is not None and pipeline.schedule.enabled:
                drafts.append({
                    "action_type": "pipeline_schedule_disable",
                    "subject": f"pipeline:{pipeline.id}",
                    "payload": {"pipeline_id": pipeline.id},
                    "confidence": 60.0,
                    "rationale": {
                        "summary": (
                            f"Pipeline '{pipeline.name}' latest run #{run.id} "
                            "failed; consider pausing its schedule until a "
                            "human investigates."
                        ),
                        "evidence": evidence,
                        "trigger": trigger,
                    },
                })
        return drafts

    # ── Supersede cleared triggers ────────────────────────────

    @staticmethod
    def _supersede_cleared(db: Session) -> int:
        superseded = 0
        open_recs = (
            db.query(AutopilotRecommendation)
            .filter(
                AutopilotRecommendation.status == "pending",
                AutopilotRecommendation.action_type.in_(ENGINE_MANAGED_TYPES),
            )
            .all()
        )
        for rec in open_recs:
            reason = None
            if rec.action_type == "connector_health_check":
                conn = (
                    db.query(DBConnection)
                    .filter(DBConnection.id == rec.payload.get("connection_id"))
                    .first()
                )
                if conn is None or conn.is_deleted:
                    reason = "connection deleted"
                elif conn.health_status == "healthy":
                    reason = "connection is healthy again"
            elif rec.action_type == "mapping_suggestions_refresh":
                m = (
                    db.query(Mapping)
                    .filter(Mapping.id == rec.payload.get("mapping_id"))
                    .first()
                )
                if m is None or m.deleted_at is not None:
                    reason = "mapping deleted"
                elif m.status != "draft":
                    reason = f"mapping is '{m.status}' — suggestions are draft-only"
            elif rec.action_type in ("notify_slack_internal", "pipeline_schedule_disable"):
                # Only supersede via the pipeline-escalation trigger clearing
                # (§ pipeline-failure-autopilot-escalation design) — these
                # action types may be reused by other future evaluators for
                # unrelated subjects, so the "pipeline:" prefix guard keeps
                # this rule scoped to recs this evaluator itself created.
                pipeline_id = _pipeline_id_from_subject(rec.subject)
                if pipeline_id is not None:
                    latest_run = (
                        db.query(PipelineRun)
                        .filter(PipelineRun.pipeline_id == pipeline_id)
                        .order_by(PipelineRun.id.desc())
                        .first()
                    )
                    if latest_run is not None and latest_run.status == "succeeded":
                        reason = "pipeline succeeded on a later run"
            if reason and AutopilotService.supersede(db, rec, reason=reason):
                superseded += 1
        return superseded

    # ── Merge same-subject drafts from different evaluators ────

    # Higher wins when two evaluators draft a recommendation for the same
    # (action_type, subject) in one sweep — e.g. a mapping that is both
    # brand new and already drift-affected. Explicit and order-independent:
    # unlike relying on evaluator call order in `evaluate_all`, this survives
    # a future evaluator being added or the call order being refactored.
    _TRIGGER_PRIORITY = {"mapping_created": 0, "schema_drift": 1}

    @staticmethod
    def _merge_drafts_by_subject(
        drafts: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        by_key: Dict[tuple, Dict[str, Any]] = {}
        for d in drafts:
            key = (d["action_type"], d["subject"])
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = d
                continue
            existing_priority = AutopilotEngine._TRIGGER_PRIORITY.get(
                existing["rationale"]["trigger"].get("kind"), 0,
            )
            new_priority = AutopilotEngine._TRIGGER_PRIORITY.get(
                d["rationale"]["trigger"].get("kind"), 0,
            )
            if new_priority >= existing_priority:
                by_key[key] = d
        return list(by_key.values())

    # ── Entrypoint ────────────────────────────────────────────

    @staticmethod
    def evaluate_all(db: Session, *, actor: str = ENGINE_ACTOR) -> Dict[str, int]:
        """Run all evaluators; dedupe, supersede cleared triggers, then hand
        newly created recommendations to the auto-dispatch decision."""
        logger.info("[pipeline] stage=autopilot_evaluate actor=%s", actor)
        drafts = AutopilotEngine._merge_drafts_by_subject(
            AutopilotEngine._evaluate_new_draft_mappings(db)
            + AutopilotEngine._evaluate_connector_health(db)
            + AutopilotEngine._evaluate_schema_drift(db)
            + AutopilotEngine._evaluate_pipeline_failures(db)
        )
        created_recs = []
        refreshed = 0
        skipped = 0
        for d in drafts:
            # bugs/02: a draft that fails registry validation (evaluator and
            # registry deployed out of sync) must not take down the whole
            # sweep — skip it, keep the rest (fail-safe Reliability NFR).
            try:
                rec, created = AutopilotService.upsert_recommendation(
                    db,
                    action_type=d["action_type"],
                    subject=d["subject"],
                    payload=d["payload"],
                    rationale=d["rationale"],
                    confidence=d["confidence"],
                    created_by=actor,
                )
            except (UnknownActionError, ProhibitedActionError,
                    PayloadValidationError) as exc:
                skipped += 1
                logger.warning(
                    "[pipeline] stage=autopilot_evaluate skipping draft "
                    "action_type=%s subject=%s: %s",
                    d.get("action_type"), d.get("subject"), exc,
                )
                continue
            if created:
                created_recs.append(rec)
            else:
                refreshed += 1

        superseded = AutopilotEngine._supersede_cleared(db)

        counts = {
            "created": len(created_recs),
            "refreshed": refreshed,
            "superseded": superseded,
            "skipped": skipped,
        }
        if counts["created"] or counts["superseded"]:
            record_audit(db, "autopilot_evaluated", actor=actor, payload=counts)
        # Make new recommendations durable BEFORE any auto-dispatch: the
        # executor task opens its own session and must see them.
        db.commit()

        auto_dispatched = 0
        for rec in created_recs:
            if AutopilotService.maybe_auto_execute(db, rec) == "auto_dispatched":
                auto_dispatched += 1
        db.commit()  # persists any policy-disabled supersedes from the loop
        counts["auto_dispatched"] = auto_dispatched
        logger.info("[pipeline] stage=autopilot_evaluate done %s", counts)
        return counts
