"""Role + permission-catalog router (DP-SEC-001, SEC-T1/SEC-T5).

All mutating endpoints require the elevated 'admin' role (FR8/AC3) — no
other role is authorized to change roles or permission grants. Destructive
operations (role delete) additionally require ?confirm=true once
dependents exist, mirroring app.api.routers.connectors' delete pattern.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.security import (
    PermissionRead,
    RoleCreate,
    RolePermissionSetRequest,
    RoleRead,
    RoleUpdate,
)
from app.services.rbac_service import PermissionCRUD, RoleCRUD

router = APIRouter()


@router.get("/permissions", response_model=List[PermissionRead])
def list_permissions(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return PermissionCRUD.list_permissions(db)


@router.get("/", response_model=List[RoleRead])
def list_roles(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return RoleCRUD.list_roles(db)


@router.post("/", response_model=RoleRead, status_code=201)
def create_role(
    req: RoleCreate, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    role = RoleCRUD.create_role(db, name=req.name, description=req.description, actor=user.email)
    return RoleCRUD.to_dict(db, role)


@router.get("/{role_id}", response_model=RoleRead)
def get_role(role_id: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    role = RoleCRUD.get_role(db, role_id)
    return RoleCRUD.to_dict(db, role)


@router.put("/{role_id}", response_model=RoleRead)
def update_role(
    role_id: int, req: RoleUpdate, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    role = RoleCRUD.update_role(
        db, role_id, name=req.name, description=req.description,
        is_active=req.is_active, actor=user.email,
    )
    return RoleCRUD.to_dict(db, role)


@router.delete("/{role_id}")
def delete_role(
    role_id: int, confirm: bool = Query(False), db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    return RoleCRUD.delete_role(db, role_id, confirm=confirm, actor=user.email)


@router.get("/{role_id}/permissions", response_model=List[int])
def get_role_permissions(role_id: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    RoleCRUD.get_role(db, role_id)
    return PermissionCRUD.list_role_permission_ids(db, role_id)


@router.put("/{role_id}/permissions", response_model=RoleRead)
def set_role_permissions(
    role_id: int, req: RolePermissionSetRequest, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    role = RoleCRUD.set_role_permissions(db, role_id, permission_ids=req.permission_ids, actor=user.email)
    return RoleCRUD.to_dict(db, role)
