"""Masking + row-access policy router (DP-SEC-001, SEC-T3/SEC-T4).

Mutating endpoints require the elevated 'admin' role (FR8). Enforcement
itself lives in app.services.viz_service (see that module for the
connection_id + table_name scoped enforcement point).
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.security import (
    MaskingPolicyCreate,
    MaskingPolicyRead,
    MaskingPolicyUpdate,
    RowAccessPolicyCreate,
    RowAccessPolicyRead,
    RowAccessPolicyUpdate,
)
from app.services.rbac_service import MaskingPolicyCRUD, RowAccessPolicyCRUD

router = APIRouter()


# ── Masking policies ──────────────────────────────────────────────────────


@router.get("/masking", response_model=List[MaskingPolicyRead])
def list_masking_policies(
    connection_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return MaskingPolicyCRUD.list_policies(db, connection_id=connection_id)


@router.post("/masking", response_model=MaskingPolicyRead, status_code=201)
def create_masking_policy(
    req: MaskingPolicyCreate, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    return MaskingPolicyCRUD.create_policy(
        db, connection_id=req.connection_id, table_name=req.table_name,
        column_name=req.column_name, masking_type=req.masking_type,
        exempt_roles=req.exempt_roles, actor=user.email,
    )


@router.put("/masking/{policy_id}", response_model=MaskingPolicyRead)
def update_masking_policy(
    policy_id: int, req: MaskingPolicyUpdate, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    return MaskingPolicyCRUD.update_policy(
        db, policy_id, masking_type=req.masking_type,
        exempt_roles=req.exempt_roles, actor=user.email,
    )


@router.delete("/masking/{policy_id}", status_code=204)
def delete_masking_policy(
    policy_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    MaskingPolicyCRUD.delete_policy(db, policy_id, actor=user.email)
    return None


# ── Row access policies ──────────────────────────────────────────────────


@router.get("/row-access", response_model=List[RowAccessPolicyRead])
def list_row_access_policies(
    connection_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return RowAccessPolicyCRUD.list_policies(db, connection_id=connection_id)


@router.post("/row-access", response_model=RowAccessPolicyRead, status_code=201)
def create_row_access_policy(
    req: RowAccessPolicyCreate, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    return RowAccessPolicyCRUD.create_policy(
        db, connection_id=req.connection_id, table_name=req.table_name,
        filter_conditions=[c.model_dump() for c in req.filter_conditions],
        applies_to_roles=req.applies_to_roles, actor=user.email,
    )


@router.put("/row-access/{policy_id}", response_model=RowAccessPolicyRead)
def update_row_access_policy(
    policy_id: int, req: RowAccessPolicyUpdate, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    return RowAccessPolicyCRUD.update_policy(
        db, policy_id,
        filter_conditions=[c.model_dump() for c in req.filter_conditions] if req.filter_conditions is not None else None,
        applies_to_roles=req.applies_to_roles, actor=user.email,
    )


@router.delete("/row-access/{policy_id}", status_code=204)
def delete_row_access_policy(
    policy_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    RowAccessPolicyCRUD.delete_policy(db, policy_id, actor=user.email)
    return None
