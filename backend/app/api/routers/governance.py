"""Governance Intelligence Center (Enterprise v2, E08).

Authoring/read endpoints only. Retention enforcement (E08-7) and
compliance reporting (E08-8) are not implemented — both are gated on
tenant isolation / legal sign-off. See governance_service.py.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.database import get_db
from app.models.user import User
from app.schemas.governance import (
    GovernanceMetadataResponse,
    GovernanceMetadataUpsert,
    GovernanceScore,
)
from app.services.governance_service import GovernanceService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/score", response_model=GovernanceScore)
def get_governance_score(
    connection_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst", "viewer")),
):
    return GovernanceService(db).get_score(connection_id=connection_id)


@router.get("/{connection_id}", response_model=list[GovernanceMetadataResponse])
def list_governance_metadata(
    connection_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    return GovernanceService(db).list_for_connection(connection_id)


@router.put("/{connection_id}", response_model=GovernanceMetadataResponse)
def upsert_governance_metadata(
    connection_id: int,
    payload: GovernanceMetadataUpsert,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    logger.info("[governance] stage=upsert_requested user=%s connection_id=%s table=%s column=%s",
                user.email, connection_id, payload.table_name, payload.column_name)
    try:
        return GovernanceService(db).upsert(
            connection_id=connection_id, table=payload.table_name, column=payload.column_name,
            owner=payload.owner, steward=payload.steward, classification=payload.classification,
            retention_policy=payload.retention_policy, compliance_status=payload.compliance_status,
            updated_by=user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
