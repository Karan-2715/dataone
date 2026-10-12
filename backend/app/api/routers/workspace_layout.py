"""Dockable workspace layout persistence (Enterprise v2, E11-8)."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.workspace_layout import WorkspaceLayoutResponse, WorkspaceLayoutUpdate
from app.services.workspace_layout_service import WorkspaceLayoutService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{workspace_key}", response_model=WorkspaceLayoutResponse)
def get_workspace_layout(
    workspace_key: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        layout = WorkspaceLayoutService.get(db, user.id, workspace_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return WorkspaceLayoutResponse(workspace_key=workspace_key, layout=layout)


@router.put("/{workspace_key}", response_model=WorkspaceLayoutResponse)
def put_workspace_layout(
    workspace_key: str,
    payload: WorkspaceLayoutUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        layout = WorkspaceLayoutService.upsert(db, user.id, workspace_key, payload.layout)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return WorkspaceLayoutResponse(workspace_key=workspace_key, layout=layout)
