"""Visualize charting API: aggregation queries + saved views
(Visualize Task #1, VIZ-T1/T5)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.viz import (
    VizQueryRequest, VizQueryResponse, VizViewCreate, VizViewListResponse, VizViewRead,
)
from app.services.viz_service import VizService

router = APIRouter()


@router.post("/query", response_model=VizQueryResponse)
def run_query(
    req: VizQueryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = VizService.run_query(
        db, connection_id=req.connection_id, table_name=req.table_name,
        dimensions=req.dimensions,
        measures=[m.model_dump() for m in req.measures],
        filters=[f.model_dump() for f in req.filters],
        requester_role=user.role,
    )
    return VizQueryResponse(**result)


@router.post("/views", response_model=VizViewRead, status_code=201)
def create_view(
    req: VizViewCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    view = VizService.create_view(
        db, actor=user.email,
        name=req.name, connection_id=req.connection_id, table_name=req.table_name,
        chart_type=req.chart_type, dimensions=req.dimensions,
        measures=req.measures, filters=req.filters,
    )
    return VizViewRead.model_validate(view)


@router.get("/views", response_model=VizViewListResponse)
def list_views(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    items, total = VizService.list_views(db)
    return VizViewListResponse(items=[VizViewRead.model_validate(v) for v in items], total=total)


@router.get("/views/{view_id}", response_model=VizViewRead)
def get_view(
    view_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return VizViewRead.model_validate(VizService.get_view(db, view_id))


@router.delete("/views/{view_id}", status_code=204)
def delete_view(
    view_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    VizService.delete_view(db, view_id, actor=user.email)
    return None
