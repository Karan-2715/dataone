"""User listing + role assignment router (DP-SEC-001, SEC-T1/SEC-T5/SEC-T6).

Assign/revoke require the elevated 'admin' role (FR2/FR8); revoking a
user's last remaining role requires ?confirm=true (it leaves them with
zero permissions under deny-by-default).
"""
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.security import (
    EffectivePermissionsResponse,
    UserRoleAssignRequest,
    UserRolesResponse,
    UserSummary,
)
from app.services.rbac_service import UserRoleService

router = APIRouter()


@router.get("/", response_model=List[UserSummary])
def list_users(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return UserRoleService.list_users(db)


@router.get("/{user_id}/roles", response_model=UserRolesResponse)
def get_user_roles(user_id: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return UserRoleService.get_user_roles(db, user_id)


@router.post("/{user_id}/roles")
def assign_role(
    user_id: int, req: UserRoleAssignRequest, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    return UserRoleService.assign_role(db, user_id, role_id=req.role_id, actor=user.email)


@router.delete("/{user_id}/roles/{role_id}")
def revoke_role(
    user_id: int, role_id: int, confirm: bool = Query(False), db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    return UserRoleService.revoke_role(db, user_id, role_id, confirm=confirm, actor=user.email)


@router.get("/{user_id}/effective-permissions", response_model=EffectivePermissionsResponse)
def effective_permissions(user_id: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return UserRoleService.effective_permissions(db, user_id)
