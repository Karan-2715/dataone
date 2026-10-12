"""Agentic DBA Copilot API (agentic_dba_tasks #3/#6/#7).

Plan generation is async (Celery) — POST /plan returns immediately with a
plan_id the chat UI polls via GET /plans/{id}. Approval executes strictly
through the existing gated write path (admin role, same bar as Query
Studio's own write gate — enforced in the execution service).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.schema_design_plan import SchemaDesignPlan
from app.models.user import User
from app.schemas.agentic_dba import (
    PlanCreateRequest,
    PlanCreateResponse,
    PlanListResponse,
    PlanResponse,
)
from app.services import agentic_dba_execution_service
from app.services.agentic_dba_engine import create_plan

logger = logging.getLogger(__name__)
router = APIRouter()


def dispatch_plan_generation(plan_id: int) -> None:
    """Celery dispatch, separated so tests can run generation inline."""
    from app.tasks.agentic_dba_tasks import generate_plan_task
    generate_plan_task.delay(plan_id)


@router.post("/plan", response_model=PlanCreateResponse, status_code=202)
def request_plan(
    req: PlanCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        plan = create_plan(
            db, question=req.question, connection_id=req.connection_id,
            session_id=req.session_id, actor=user.email,
            target_connection_id=req.target_connection_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    dispatch_plan_generation(plan.id)
    return PlanCreateResponse(plan_id=plan.id, status=plan.status)


@router.get("/plans/{plan_id}", response_model=PlanResponse)
def get_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    plan = db.query(SchemaDesignPlan).filter(SchemaDesignPlan.id == plan_id).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@router.get("/plans", response_model=PlanListResponse)
def list_plans(
    session_id: Optional[str] = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(SchemaDesignPlan).order_by(SchemaDesignPlan.created_at.desc())
    if session_id:
        q = q.filter(SchemaDesignPlan.session_id == session_id)
    return PlanListResponse(plans=q.limit(50).all())


@router.post("/plans/{plan_id}/approve", response_model=PlanResponse)
def approve_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Approve + apply. Role gate (admin) lives in the execution service —
    the same bar Query Studio's write path enforces, not a parallel one."""
    return agentic_dba_execution_service.approve_and_execute_plan(
        db, plan_id, actor=user.email, role=user.role,
    )


@router.post("/plans/{plan_id}/reject", response_model=PlanResponse)
def reject_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return agentic_dba_execution_service.reject_plan(db, plan_id, actor=user.email)
