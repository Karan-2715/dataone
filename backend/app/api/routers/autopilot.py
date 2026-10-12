"""Autopilot router: legacy run console + governance layer (DP-AUTO-001).

Security (ai_autopilot_tasks #1): every endpoint requires authentication.
Before 2026-07-08 this router was completely unauthenticated — including
``POST /run`` with ``mode="execute"``, which copies data into the target.

Governance (ai_autopilot_tasks #2/#5/#6): policy, recommendations,
approve/reject/modify, action log, evaluate-now. ``mode="execute"`` no
longer executes directly — it enters the approval queue (FR3/AC2).
"""
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.autopilot import AutopilotLog, AutopilotRun
from app.models.connection import DBConnection
from app.models.user import User
from app.services.audit_helper import record_audit
from app.services.autopilot_engine import AutopilotEngine
from app.services.autopilot_service import AutopilotService

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Legacy run console (auth-gated; execute rerouted) ─────────────────────


class RunAutopilotRequest(BaseModel):
    source_id: int
    target_id: int
    mode: str = "suggest"  # suggest | execute
    model: str = "llama3"


@router.post("/run")
def start_autopilot_run(
    req: RunAutopilotRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    """Start a suggest-mode analysis run, or queue an execute-mode run for
    approval (FR3 — execution never happens straight off this endpoint)."""
    if req.mode not in ("suggest", "execute"):
        raise HTTPException(status_code=422, detail="mode must be 'suggest' or 'execute'")
    source_conn = db.query(DBConnection).filter(DBConnection.id == req.source_id).first()
    target_conn = db.query(DBConnection).filter(DBConnection.id == req.target_id).first()
    if not source_conn or not target_conn:
        raise HTTPException(status_code=404, detail="Source or target connection not found")
    if req.source_id == req.target_id:
        raise HTTPException(status_code=400, detail="Source and target must be different connections")

    if req.mode == "execute":
        # Irreversible (writes rows into the target) ⇒ approval queue, always.
        rec, created = AutopilotService.upsert_recommendation(
            db,
            action_type="migration_execute",
            subject=f"migration:{req.source_id}->{req.target_id}",
            payload={"source_id": req.source_id, "target_id": req.target_id,
                     "model": req.model},
            rationale={
                "summary": (
                    f"User-requested migration execution from "
                    f"'{source_conn.name}' into '{target_conn.name}'. "
                    "Writes rows into the target — requires explicit approval."
                ),
                "evidence": [f"requested_by={user.email}"],
                "trigger": {"kind": "user_request"},
            },
            confidence=100.0,
            created_by=user.email,
        )
        db.commit()
        logger.info(
            "Autopilot execute request queued for approval (rec=%s, source=%s, target=%s)",
            rec.id, req.source_id, req.target_id,
        )
        return {
            "status": "queued_for_approval",
            "recommendation_id": rec.id,
            "already_pending": not created,
        }

    run_id = str(uuid.uuid4())
    run = AutopilotRun(
        id=run_id,
        source_id=req.source_id,
        target_id=req.target_id,
        mode="suggest",
        model=req.model,
        status="running",
    )
    db.add(run)
    record_audit(
        db, "autopilot_run_started", actor=user.email,
        connection_id=req.source_id,
        payload={"run_id": run_id, "mode": "suggest", "target_id": req.target_id},
    )
    db.commit()

    from app.tasks.ai_tasks import run_autopilot_task
    run_autopilot_task.delay(run_id=run_id, source_id=req.source_id,
                             target_id=req.target_id, mode="suggest")
    logger.info("Autopilot run %s started (source=%s, target=%s, mode=suggest)",
                run_id, req.source_id, req.target_id)
    return {"run_id": run_id, "status": "running"}


@router.get("/runs")
def list_runs(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List the last 20 autopilot runs."""
    runs = (
        db.query(AutopilotRun)
        .order_by(AutopilotRun.started_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": r.id,
            "source_id": r.source_id,
            "target_id": r.target_id,
            "mode": r.mode,
            "model": r.model,
            "status": r.status,
            "started_at": r.started_at,
            "completed_at": r.completed_at,
        }
        for r in runs
    ]


@router.get("/runs/{run_id}/logs")
def get_run_logs(
    run_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return all log entries for an autopilot run (for console streaming)."""
    run = db.query(AutopilotRun).filter(AutopilotRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    logs = (
        db.query(AutopilotLog)
        .filter(AutopilotLog.run_id == run_id)
        .order_by(AutopilotLog.created_at.asc())
        .all()
    )
    return {
        "run_id": run_id,
        "status": run.status,
        "logs": [
            {
                "step": lg.step,
                "message": lg.message,
                "level": lg.level,
                "created_at": lg.created_at,
            }
            for lg in logs
        ],
    }


@router.get("/runs/{run_id}/status")
def get_run_status(
    run_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Summary status of an autopilot run."""
    run = db.query(AutopilotRun).filter(AutopilotRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "run_id": run_id,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "result_summary": run.result_summary,
    }


# ── Governance: policy (FR1) ──────────────────────────────────────────────


class PolicyUpdateRequest(BaseModel):
    autonomy: str
    max_auto_per_hour: Optional[int] = None


@router.get("/policy")
def get_policy(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return {"policies": AutopilotService.get_policies(db)}


@router.put("/policy/{action_type}")
def put_policy(
    action_type: str,
    req: PolicyUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    return AutopilotService.put_policy(
        db, action_type,
        autonomy=req.autonomy,
        max_auto_per_hour=req.max_auto_per_hour,
        actor=user.email,
    )


# ── Governance: recommendations + approval queue (FR2/FR3/FR7) ───────────


class RejectRequest(BaseModel):
    reason: Optional[str] = None


class ModifyRequest(BaseModel):
    payload: Dict[str, Any]


@router.get("/recommendations")
def list_recommendations(
    status: Optional[str] = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # status="all" (or empty) disables the filter.
    effective = None if status in (None, "", "all") else status
    return AutopilotService.list_recommendations(
        db, status=effective, limit=limit, offset=offset,
    )


@router.post("/recommendations/{rec_id}/approve")
def approve_recommendation(
    rec_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    rec = AutopilotService.approve(db, rec_id, actor=user.email)
    return AutopilotService.rec_to_dict(rec)


@router.post("/recommendations/{rec_id}/reject")
def reject_recommendation(
    rec_id: int,
    req: Optional[RejectRequest] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    rec = AutopilotService.reject(
        db, rec_id, actor=user.email,
        reason=req.reason if req else None,
    )
    return AutopilotService.rec_to_dict(rec)


@router.post("/recommendations/{rec_id}/modify")
def modify_recommendation(
    rec_id: int,
    req: ModifyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    rec = AutopilotService.modify(db, rec_id, payload=req.payload, actor=user.email)
    return AutopilotService.rec_to_dict(rec)


# ── Governance: action log (FR6) + evaluate-now (FR2) ─────────────────────


@router.get("/actions")
def list_actions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return AutopilotService.list_actions(db, limit=limit, offset=offset)


@router.post("/evaluate")
def evaluate_now(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    """Run trigger evaluation synchronously (DB-only, fast)."""
    counts = AutopilotEngine.evaluate_all(db, actor=user.email)
    return {"status": "completed", **counts}
