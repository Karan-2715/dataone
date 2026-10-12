"""Autopilot governance service: policy, approval queue, bounded executor.

Covers ai_autopilot_tasks #2 (policy), #6 (queue + executor), #7 (rate
limits + circuit breaker), #8 (audit emission). Guardrails themselves live
in ``autopilot_registry`` — this module enforces them on every path.

Transition safety: all status changes use guarded UPDATEs
(``WHERE status == expected``) so concurrent approvals / double dispatch are
idempotent — the loser matches 0 rows and stops (same pattern as the mapping
publish race fix).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.autopilot import (
    AutopilotActionLog,
    AutopilotPolicy,
    AutopilotRecommendation,
)
from app.services.audit_helper import record_audit
from app.services.autopilot_registry import (
    ACTION_REGISTRY,
    ActionSpec,
    DISPATCH_AFTER_COMMIT_KEY,
    PayloadValidationError,
    ProhibitedActionError,
    UnknownActionError,
    check_action_allowed,
    validate_payload,
)

logger = logging.getLogger(__name__)

AUTONOMY_LEVELS = ("disabled", "suggest", "approve", "auto")
OPEN_STATUS = "pending"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AutopilotService:

    # ── Policy (FR1) ──────────────────────────────────────────

    @staticmethod
    def get_effective_policy(db: Session, action_type: str) -> Dict[str, Any]:
        """Policy row merged over fail-safe defaults (suggest)."""
        row = (
            db.query(AutopilotPolicy)
            .filter(AutopilotPolicy.action_type == action_type)
            .first()
        )
        return {
            "action_type": action_type,
            "autonomy": row.autonomy if row else "suggest",
            "max_auto_per_hour": (
                row.max_auto_per_hour if row
                else settings.AUTOPILOT_TYPE_AUTO_LIMIT_PER_HOUR
            ),
            "updated_by": row.updated_by if row else None,
        }

    @staticmethod
    def get_policies(db: Session) -> List[Dict[str, Any]]:
        """Full taxonomy — one entry per registry action, DB rows merged over
        defaults, registry metadata included so the UI needs one call."""
        out = []
        for spec in ACTION_REGISTRY.values():
            policy = AutopilotService.get_effective_policy(db, spec.action_type)
            out.append({
                **policy,
                "description": spec.description,
                "risk": spec.risk,
                "reversible": spec.reversible,
                "reversibility_note": spec.reversibility_note,
                "auto_capable": spec.auto_capable,
            })
        return out

    @staticmethod
    def put_policy(db: Session, action_type: str, *, autonomy: str,
                   max_auto_per_hour: Optional[int], actor: str) -> Dict[str, Any]:
        try:
            spec = check_action_allowed(action_type)
        except ProhibitedActionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except UnknownActionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if autonomy not in AUTONOMY_LEVELS:
            raise HTTPException(
                status_code=422,
                detail=f"autonomy must be one of {list(AUTONOMY_LEVELS)}",
            )
        if autonomy == "auto" and not spec.auto_capable:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"'{action_type}' is not auto-capable "
                    f"(risk={spec.risk}, reversible={spec.reversible}); "
                    "highest allowed autonomy is 'approve'"
                ),
            )
        if max_auto_per_hour is not None and max_auto_per_hour < 0:
            raise HTTPException(
                status_code=422, detail="max_auto_per_hour must be >= 0",
            )

        before = AutopilotService.get_effective_policy(db, action_type)
        row = (
            db.query(AutopilotPolicy)
            .filter(AutopilotPolicy.action_type == action_type)
            .first()
        )
        if row is None:
            row = AutopilotPolicy(action_type=action_type)
            db.add(row)
        row.autonomy = autonomy
        if max_auto_per_hour is not None:
            row.max_auto_per_hour = max_auto_per_hour
        row.updated_by = actor
        db.flush()
        record_audit(
            db, "autopilot_policy_changed", actor=actor,
            payload={
                "action_type": action_type,
                "before": {k: before[k] for k in ("autonomy", "max_auto_per_hour")},
                "after": {"autonomy": row.autonomy,
                          "max_auto_per_hour": row.max_auto_per_hour},
            },
        )
        db.commit()
        return AutopilotService.get_effective_policy(db, action_type)

    # ── Recommendations: create / dedupe / supersede (FR2) ────

    @staticmethod
    def upsert_recommendation(db: Session, *, action_type: str, subject: str,
                              payload: Dict[str, Any], rationale: Dict[str, Any],
                              confidence: float, created_by: str,
                              ) -> tuple[AutopilotRecommendation, bool]:
        """Create a pending recommendation, or refresh the open one for the
        same dedupe key (INDEX decision 7 — never duplicate an open question).
        Returns (rec, created)."""
        spec = check_action_allowed(action_type)
        payload = validate_payload(spec, payload)
        dedupe_key = f"{action_type}:{subject}"
        existing = (
            db.query(AutopilotRecommendation)
            .filter(
                AutopilotRecommendation.dedupe_key == dedupe_key,
                AutopilotRecommendation.status == OPEN_STATUS,
            )
            .first()
        )
        if existing:
            existing.rationale = rationale
            existing.confidence = confidence
            existing.payload = payload
            db.flush()
            return existing, False

        rec = AutopilotRecommendation(
            action_type=action_type,
            payload=payload,
            subject=subject,
            dedupe_key=dedupe_key,
            rationale=rationale,
            confidence=confidence,
            risk=spec.risk,
            reversible=spec.reversible,
            reversibility_note=spec.reversibility_note,
            status="pending",
            created_by=created_by,
        )
        db.add(rec)
        db.flush()
        record_audit(
            db, "autopilot_recommendation_created", actor=created_by,
            payload={
                "recommendation_id": rec.id, "action_type": action_type,
                "subject": subject, "confidence": confidence,
                "rationale_summary": rationale.get("summary"),
            },
        )
        # Notify-out (aci_integration_tasks #5): per-type opt-in, fire-and-
        # forget — a notification failure never affects this write. The
        # approval decision itself stays inside DataOne; the message only
        # links back to the approval-queue UI.
        from app.services.notification_service import dispatch_notify_out
        dispatch_notify_out(
            db, event_key=f"autopilot:{action_type}",
            title=f"Autopilot recommendation pending approval: {action_type}",
            body=f"{subject} — {rationale.get('summary', '')}"[:500],
            link=f"{settings.DATAONE_BASE_URL}/dashboard/autopilot",
        )
        return rec, True

    @staticmethod
    def supersede(db: Session, rec: AutopilotRecommendation, *, reason: str,
                  actor: str = "autopilot-engine") -> bool:
        """Close an open recommendation whose trigger cleared / policy disabled."""
        updated = (
            db.query(AutopilotRecommendation)
            .filter(
                AutopilotRecommendation.id == rec.id,
                AutopilotRecommendation.status == OPEN_STATUS,
            )
            .update(
                {"status": "superseded", "decided_by": actor,
                 "decided_at": _now(), "decision_mode": "auto"},
                synchronize_session=False,
            )
        )
        if updated:
            # Same identity-map staleness class as bugs/03: make the next
            # attribute access re-read the row (within the caller's tx).
            db.expire(rec)
            record_audit(
                db, "autopilot_recommendation_superseded", actor=actor,
                payload={"recommendation_id": rec.id,
                         "action_type": rec.action_type, "reason": reason},
            )
        return bool(updated)

    # ── Queue reads ───────────────────────────────────────────

    @staticmethod
    def list_recommendations(db: Session, *, status: Optional[str],
                             limit: int, offset: int) -> Dict[str, Any]:
        q = db.query(AutopilotRecommendation)
        if status:
            q = q.filter(AutopilotRecommendation.status == status)
        total = q.count()
        items = (
            q.order_by(AutopilotRecommendation.created_at.desc(),
                       AutopilotRecommendation.id.desc())
            .offset(offset).limit(limit).all()
        )
        return {"total": total,
                "items": [AutopilotService.rec_to_dict(r) for r in items]}

    @staticmethod
    def list_actions(db: Session, *, limit: int, offset: int) -> Dict[str, Any]:
        q = db.query(AutopilotActionLog)
        total = q.count()
        items = (
            q.order_by(AutopilotActionLog.started_at.desc(),
                       AutopilotActionLog.id.desc())
            .offset(offset).limit(limit).all()
        )
        return {
            "total": total,
            "items": [
                {
                    "id": a.id,
                    "recommendation_id": a.recommendation_id,
                    "action_type": a.action_type,
                    "payload": a.payload,
                    "mode": a.mode,
                    "outcome": a.outcome,
                    "detail": a.detail,
                    "reversibility_note": a.reversibility_note,
                    "actor": a.actor,
                    "started_at": a.started_at,
                    "finished_at": a.finished_at,
                }
                for a in items
            ],
        }

    @staticmethod
    def rec_to_dict(r: AutopilotRecommendation) -> Dict[str, Any]:
        return {
            "id": r.id,
            "action_type": r.action_type,
            "payload": r.payload,
            "subject": r.subject,
            "rationale": r.rationale,
            "confidence": r.confidence,
            "risk": r.risk,
            "reversible": r.reversible,
            "reversibility_note": r.reversibility_note,
            "status": r.status,
            "created_by": r.created_by,
            "created_at": r.created_at,
            "decided_by": r.decided_by,
            "decided_at": r.decided_at,
            "decision_mode": r.decision_mode,
            "modified_by": r.modified_by,
            "modified_at": r.modified_at,
            "execution_result": r.execution_result,
        }

    # ── Decisions (FR3/FR7) ───────────────────────────────────

    @staticmethod
    def _get_rec(db: Session, rec_id: int) -> AutopilotRecommendation:
        rec = (
            db.query(AutopilotRecommendation)
            .filter(AutopilotRecommendation.id == rec_id)
            .first()
        )
        if not rec:
            raise HTTPException(status_code=404, detail="recommendation not found")
        return rec

    @staticmethod
    def approve(db: Session, rec_id: int, *, actor: str) -> AutopilotRecommendation:
        rec = AutopilotService._get_rec(db, rec_id)
        updated = (
            db.query(AutopilotRecommendation)
            .filter(
                AutopilotRecommendation.id == rec_id,
                AutopilotRecommendation.status == OPEN_STATUS,
            )
            .update(
                {"status": "approved", "decided_by": actor,
                 "decided_at": _now(), "decision_mode": "human"},
                synchronize_session=False,
            )
        )
        if not updated:
            raise HTTPException(
                status_code=409,
                detail=f"recommendation {rec_id} is '{rec.status}'; only pending "
                       "recommendations can be approved",
            )
        record_audit(
            db, "autopilot_recommendation_approved", actor=actor,
            payload={"recommendation_id": rec_id, "action_type": rec.action_type},
        )
        db.commit()
        db.refresh(rec)

        from app.tasks.autopilot_tasks import execute_recommendation_task
        execute_recommendation_task.delay(recommendation_id=rec_id, auto=False)
        return rec

    @staticmethod
    def reject(db: Session, rec_id: int, *, actor: str,
               reason: Optional[str]) -> AutopilotRecommendation:
        rec = AutopilotService._get_rec(db, rec_id)
        updated = (
            db.query(AutopilotRecommendation)
            .filter(
                AutopilotRecommendation.id == rec_id,
                AutopilotRecommendation.status == OPEN_STATUS,
            )
            .update(
                {"status": "rejected", "decided_by": actor,
                 "decided_at": _now(), "decision_mode": "human"},
                synchronize_session=False,
            )
        )
        if not updated:
            raise HTTPException(
                status_code=409,
                detail=f"recommendation {rec_id} is '{rec.status}'; only pending "
                       "recommendations can be rejected",
            )
        record_audit(
            db, "autopilot_recommendation_rejected", actor=actor,
            payload={"recommendation_id": rec_id,
                     "action_type": rec.action_type, "reason": reason},
        )
        db.commit()
        db.refresh(rec)
        return rec

    @staticmethod
    def modify(db: Session, rec_id: int, *, payload: Dict[str, Any],
               actor: str) -> AutopilotRecommendation:
        """FR7 'modify': edit the action payload while pending, then approve."""
        rec = AutopilotService._get_rec(db, rec_id)
        if rec.status != OPEN_STATUS:
            raise HTTPException(
                status_code=409,
                detail=f"recommendation {rec_id} is '{rec.status}'; only pending "
                       "recommendations can be modified",
            )
        spec = check_action_allowed(rec.action_type)
        try:
            normalized = validate_payload(spec, payload)
        except PayloadValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        before = rec.payload
        rec.payload = normalized
        rec.modified_by = actor
        rec.modified_at = _now()
        db.flush()
        record_audit(
            db, "autopilot_recommendation_modified", actor=actor,
            payload={"recommendation_id": rec_id, "action_type": rec.action_type,
                     "before": before, "after": normalized},
        )
        db.commit()
        db.refresh(rec)
        return rec

    # ── Rate limits + circuit breaker (FR8) ───────────────────

    @staticmethod
    def count_auto_actions(db: Session, *, action_type: Optional[str] = None,
                           window_minutes: int = 60) -> int:
        since = _now() - timedelta(minutes=window_minutes)
        q = (
            db.query(AutopilotActionLog)
            .filter(
                AutopilotActionLog.mode == "auto",
                AutopilotActionLog.started_at >= since,
                AutopilotActionLog.outcome.in_(("success", "failure")),
            )
        )
        if action_type:
            q = q.filter(AutopilotActionLog.action_type == action_type)
        return q.count()

    @staticmethod
    def breaker_open(db: Session, action_type: str) -> bool:
        """Open when the last N auto attempts for the type (within the window)
        all failed. Computed from the action log — never stored, never mutates
        policy (INDEX decision 6)."""
        threshold = settings.AUTOPILOT_BREAKER_THRESHOLD
        since = _now() - timedelta(minutes=settings.AUTOPILOT_BREAKER_WINDOW_MINUTES)
        last = (
            db.query(AutopilotActionLog)
            .filter(
                AutopilotActionLog.action_type == action_type,
                AutopilotActionLog.mode == "auto",
                AutopilotActionLog.started_at >= since,
                AutopilotActionLog.outcome.in_(("success", "failure")),
            )
            .order_by(AutopilotActionLog.started_at.desc(),
                      AutopilotActionLog.id.desc())
            .limit(threshold)
            .all()
        )
        if len(last) < threshold:
            return False
        return all(a.outcome == "failure" for a in last)

    # ── Bounded executor (FR4/FR5, AC3/AC4) ───────────────────

    @staticmethod
    def _log_action(db: Session, *, rec: Optional[AutopilotRecommendation],
                    action_type: str, payload: Dict[str, Any], mode: str,
                    outcome: str, detail: Optional[Dict[str, Any]],
                    reversibility_note: Optional[str], actor: str,
                    started_at: datetime) -> AutopilotActionLog:
        row = AutopilotActionLog(
            recommendation_id=rec.id if rec else None,
            action_type=action_type,
            payload=payload,
            mode=mode,
            outcome=outcome,
            detail=detail,
            reversibility_note=reversibility_note,
            actor=actor,
            started_at=started_at,
            finished_at=_now(),
        )
        db.add(row)
        db.flush()
        return row

    @staticmethod
    def _demote_to_queue(db: Session, rec: AutopilotRecommendation, *,
                         outcome: str, event_type: str, reason: str) -> Dict[str, Any]:
        """Auto path refused by limits/breaker/policy: return the rec to the
        approval queue instead of executing (fail-safe, never silently drop)."""
        started = _now()
        # bugs/05: blocked_by is the structured, query-stable key; the
        # human-readable reason may be reworded without breaking audits.
        blocked_by = outcome.replace("blocked_", "")
        AutopilotService._log_action(
            db, rec=rec, action_type=rec.action_type, payload=rec.payload,
            mode="auto", outcome=outcome,
            detail={"blocked_by": blocked_by, "reason": reason},
            reversibility_note=rec.reversibility_note,
            actor="autopilot-policy", started_at=started,
        )
        db.query(AutopilotRecommendation).filter(
            AutopilotRecommendation.id == rec.id,
            AutopilotRecommendation.status == "executing",
        ).update(
            {"status": "pending", "decided_by": None, "decided_at": None,
             "decision_mode": None},
            synchronize_session=False,
        )
        record_audit(
            db, event_type, actor="autopilot-policy",
            payload={"recommendation_id": rec.id,
                     "action_type": rec.action_type,
                     "blocked_by": blocked_by, "reason": reason},
        )
        db.commit()
        # bugs/03: the bulk update above bypasses the identity map — refresh
        # so same-session readers don't see a stale 'executing' object.
        db.refresh(rec)
        logger.info("[pipeline] stage=autopilot_execute rec=%s demoted: %s",
                    rec.id, reason)
        return {"status": "demoted", "reason": reason}

    @staticmethod
    def execute_recommendation(db: Session, recommendation_id: int, *,
                               auto: bool) -> Dict[str, Any]:
        """Single execution path for both auto and approved actions.

        Idempotent: the guarded transition to 'executing' means a duplicate
        dispatch matches 0 rows and exits without side effects.
        """
        logger.info("[pipeline] stage=autopilot_execute rec=%s auto=%s",
                    recommendation_id, auto)
        rec = (
            db.query(AutopilotRecommendation)
            .filter(AutopilotRecommendation.id == recommendation_id)
            .first()
        )
        if not rec:
            return {"status": "skipped", "reason": "recommendation not found"}

        expected = OPEN_STATUS if auto else "approved"
        values: Dict[str, Any] = {"status": "executing"}
        if auto:
            values.update({"decided_by": "autopilot-policy",
                           "decided_at": _now(), "decision_mode": "auto"})
        updated = (
            db.query(AutopilotRecommendation)
            .filter(
                AutopilotRecommendation.id == recommendation_id,
                AutopilotRecommendation.status == expected,
            )
            .update(values, synchronize_session=False)
        )
        if not updated:
            db.rollback()
            return {"status": "skipped",
                    "reason": f"not in '{expected}' state (concurrent decision?)"}
        db.commit()
        db.refresh(rec)

        actor = rec.decided_by or "autopilot-policy"
        started = _now()

        # Guardrails first — regardless of policy configuration (AC3).
        try:
            spec = check_action_allowed(rec.action_type)
        except (ProhibitedActionError, UnknownActionError) as exc:
            AutopilotService._log_action(
                db, rec=rec, action_type=rec.action_type, payload=rec.payload,
                mode="auto" if auto else "approved",
                outcome="blocked_prohibited",
                detail={"blocked_by": "prohibited", "error": str(exc)},
                reversibility_note=rec.reversibility_note,
                actor=actor, started_at=started,
            )
            db.query(AutopilotRecommendation).filter(
                AutopilotRecommendation.id == rec.id,
            ).update(
                {"status": "failed", "execution_result": {"blocked": str(exc)}},
                synchronize_session=False,
            )
            record_audit(
                db, "autopilot_action_blocked", actor=actor, status="failure",
                payload={"recommendation_id": rec.id,
                         "action_type": rec.action_type,
                         "blocked_by": "prohibited", "error": str(exc)},
            )
            db.commit()
            db.refresh(rec)
            return {"status": "blocked_prohibited", "error": str(exc)}

        # Auto-only bounds (FR4/FR8): policy still auto, spec still
        # auto-capable, breaker closed, limits not exceeded.
        if auto:
            policy = AutopilotService.get_effective_policy(db, rec.action_type)
            if policy["autonomy"] != "auto" or not spec.auto_capable:
                return AutopilotService._demote_to_queue(
                    db, rec, outcome="blocked_policy",
                    event_type="autopilot_auto_demoted",
                    reason=(
                        f"policy autonomy is '{policy['autonomy']}' / "
                        f"auto_capable={spec.auto_capable} at execution time"
                    ),
                )
            if AutopilotService.breaker_open(db, rec.action_type):
                return AutopilotService._demote_to_queue(
                    db, rec, outcome="blocked_breaker",
                    event_type="autopilot_circuit_breaker_open",
                    reason=(
                        f"circuit breaker open: last "
                        f"{settings.AUTOPILOT_BREAKER_THRESHOLD} auto attempts failed"
                    ),
                )
            per_type = AutopilotService.count_auto_actions(
                db, action_type=rec.action_type, window_minutes=60,
            )
            global_count = AutopilotService.count_auto_actions(
                db, window_minutes=60,
            )
            if per_type >= policy["max_auto_per_hour"]:
                return AutopilotService._demote_to_queue(
                    db, rec, outcome="blocked_rate_limit",
                    event_type="autopilot_rate_limited",
                    reason=(
                        f"per-type limit reached ({per_type}/"
                        f"{policy['max_auto_per_hour']} auto actions in the last hour)"
                    ),
                )
            if global_count >= settings.AUTOPILOT_GLOBAL_AUTO_LIMIT_PER_HOUR:
                return AutopilotService._demote_to_queue(
                    db, rec, outcome="blocked_rate_limit",
                    event_type="autopilot_rate_limited",
                    reason=(
                        f"global limit reached ({global_count}/"
                        f"{settings.AUTOPILOT_GLOBAL_AUTO_LIMIT_PER_HOUR} "
                        "auto actions in the last hour)"
                    ),
                )

        # Payload boundary check (modify path already validates; this covers
        # rows written before a registry change).
        dispatch_after_commit = None
        try:
            payload = validate_payload(spec, rec.payload or {})
        except PayloadValidationError as exc:
            outcome, detail = "failure", {"error": f"payload invalid: {exc}"}
            result: Dict[str, Any] = {"status": "failed", "error": str(exc)}
        else:
            try:
                detail = spec.execute(db, payload, actor)
                # bugs/01: executors never commit; side effects that must
                # happen only after the transaction lands (Celery dispatch)
                # come back as a callable under this reserved key.
                dispatch_after_commit = detail.pop(DISPATCH_AFTER_COMMIT_KEY, None)
                outcome = "success"
                result = {"status": "executed", "detail": detail}
            except Exception as exc:  # clean failure — never crash the worker
                logger.warning(
                    "[pipeline] stage=autopilot_execute rec=%s failed: %s",
                    rec.id, exc,
                )
                db.rollback()
                outcome, detail = "failure", {"error": str(exc)}
                result = {"status": "failed", "error": str(exc)}

        AutopilotService._log_action(
            db, rec=rec, action_type=rec.action_type, payload=rec.payload,
            mode="auto" if auto else "approved", outcome=outcome, detail=detail,
            reversibility_note=rec.reversibility_note,
            actor=actor, started_at=started,
        )
        db.query(AutopilotRecommendation).filter(
            AutopilotRecommendation.id == rec.id,
        ).update(
            {"status": "executed" if outcome == "success" else "failed",
             "execution_result": detail},
            synchronize_session=False,
        )
        record_audit(
            db, "autopilot_action_executed", actor=actor,
            status="success" if outcome == "success" else "failure",
            payload={"recommendation_id": rec.id, "action_type": rec.action_type,
                     "mode": "auto" if auto else "approved", "outcome": outcome},
        )
        db.commit()
        db.refresh(rec)  # bugs/03: bulk update bypassed the identity map

        if dispatch_after_commit is not None:
            # Strictly after commit: the worker's session can now see every
            # row this transaction wrote (bugs/01). A dispatch failure here
            # is surfaced, not swallowed — the action row already says
            # success, so record the divergence explicitly.
            try:
                dispatch_after_commit()
            except Exception as exc:
                logger.error(
                    "[pipeline] stage=autopilot_execute rec=%s post-commit "
                    "dispatch failed: %s", rec.id, exc,
                )
                record_audit(
                    db, "autopilot_dispatch_failed", actor=actor,
                    status="failure",
                    payload={"recommendation_id": rec.id,
                             "action_type": rec.action_type,
                             "error": str(exc)},
                )
                db.commit()
                result = {"status": "executed_dispatch_failed",
                          "error": str(exc)}
        return result

    # ── Auto-dispatch decision (AC1/AC2/AC4 entrypoint) ───────

    @staticmethod
    def maybe_auto_execute(db: Session, rec: AutopilotRecommendation) -> str:
        """Called by the engine for each *newly created* recommendation.
        suggest/approve → stays pending (human queue). disabled → superseded.
        auto (and auto-capable) → dispatch the executor task."""
        policy = AutopilotService.get_effective_policy(db, rec.action_type)
        spec = ACTION_REGISTRY[rec.action_type]
        if policy["autonomy"] == "disabled":
            AutopilotService.supersede(
                db, rec, reason="policy is 'disabled' for this action type",
            )
            return "superseded"
        if policy["autonomy"] == "auto" and spec.auto_capable:
            from app.tasks.autopilot_tasks import execute_recommendation_task
            execute_recommendation_task.delay(
                recommendation_id=rec.id, auto=True,
            )
            return "auto_dispatched"
        return "pending"
