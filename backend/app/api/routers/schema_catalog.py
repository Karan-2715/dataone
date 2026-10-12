"""Schema Intel catalog API: persisted discovery, search, profiling, and
manual classification override (Tasks #1, #2, #4, #7)."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.database import get_db
from app.models.schema_catalog import CatalogColumn, CatalogTable
from app.models.user import User
from app.schemas.schema_catalog import (
    CatalogColumnResponse, CatalogForeignKeyResponse, CatalogTableListResponse,
    CatalogTableResponse, ClassificationOverrideRequest, ColumnClassificationResponse,
    ColumnProfileResponse, ProfileEnqueueResult, ScanResult,
)
from app.services.schema_catalog_service import SchemaCatalogService

router = APIRouter()


def _column_response(col: CatalogColumn) -> CatalogColumnResponse:
    return CatalogColumnResponse(
        id=col.id,
        column_name=col.column_name,
        data_type=col.data_type,
        nullable=col.nullable,
        is_primary_key=col.is_primary_key,
        ordinal_position=col.ordinal_position,
        foreign_keys=[
            CatalogForeignKeyResponse(
                references_table=fk.references_table,
                references_column=fk.references_column,
            )
            for fk in col.foreign_keys_rel
        ],
        profile=ColumnProfileResponse.model_validate(col.profile) if col.profile else None,
        classification=(
            ColumnClassificationResponse.model_validate(col.classification)
            if col.classification else None
        ),
    )


def _table_response(table: CatalogTable) -> CatalogTableResponse:
    return CatalogTableResponse(
        id=table.id,
        connection_id=table.connection_id,
        table_name=table.table_name,
        last_scanned_at=table.last_scanned_at,
        columns=[_column_response(c) for c in sorted(table.columns, key=lambda c: c.ordinal_position)],
    )


@router.get("/all-tables")
def list_all_catalog_tables(
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    request: Request = None,
):
    """Return catalog tables for the current user's Databricks connections.

    Scoping rules:
    - Returns connections owned by the logged-in user (owner_email = user.email)
    - Falls back to shared connections (owner_email IS NULL) if no user-specific ones exist
    Used by the Unity Catalog browser tab.
    """
    from sqlalchemy.orm import joinedload as jl
    from collections import defaultdict
    from app.models.connection import DBConnection
    from fastapi import Request as FastAPIRequest

    # Resolve caller email from DataOne JWT (best-effort, non-blocking)
    caller_email: Optional[str] = None
    try:
        if request is not None:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                from app.services.auth_service import AuthService
                payload = AuthService.verify_token(token)
                caller_email = payload.get("sub") if payload else None
    except Exception:
        pass  # anonymous — fall back to shared tables

    # Build connection filter: prefer user-specific, fall back to shared (NULL owner)
    conn_query = db.query(DBConnection).filter(DBConnection.is_deleted == False)  # noqa: E712
    if caller_email:
        conn_query = conn_query.filter(
            (DBConnection.owner_email == caller_email) | (DBConnection.owner_email.is_(None))
        )
    user_conn_ids = [c.id for c in conn_query.all()]

    if not user_conn_ids:
        return {"total": 0, "connections": []}

    query = (
        db.query(CatalogTable)
        .options(jl(CatalogTable.columns))
        .filter(CatalogTable.connection_id.in_(user_conn_ids))
    )
    if q:
        needle = f"%{q.lower()}%"
        query = query.filter(CatalogTable.table_name.ilike(needle))

    tables = query.order_by(CatalogTable.table_name).all()

    by_conn: dict = defaultdict(list)
    for t in tables:
        by_conn[t.connection_id].append(_table_response(t))

    # Sort: user-specific connections first, shared (null owner) last
    conn_objs = db.query(DBConnection).filter(DBConnection.id.in_(list(by_conn.keys()))).all()
    conn_map = {c.id: c for c in conn_objs}

    def sort_key(cid: int) -> tuple:
        c = conn_map.get(cid)
        is_user_owned = c.owner_email == caller_email if c else False
        return (0 if is_user_owned else 1, conn_map[cid].name if cid in conn_map else "")

    sorted_conn_ids = sorted(by_conn.keys(), key=sort_key)

    return {
        "total": len(tables),
        "connections": [
            {
                "connection_id": cid,
                "connection_name": conn_map[cid].name if cid in conn_map else f"Connection {cid}",
                "connection_type": conn_map[cid].type if cid in conn_map else "unknown",
                "is_personal": conn_map[cid].owner_email == caller_email if cid in conn_map else False,
                "tables": by_conn[cid],
            }
            for cid in sorted_conn_ids
        ],
    }

@router.post("/scan/{connection_id}", response_model=ScanResult, status_code=201)
def scan_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    request: Request = None,
):
    """Trigger a schema scan for a connection.

    Access rules:
    - Any authenticated user can scan a connection they own (owner_email = their email)
    - Admin / analyst can scan any connection
    """
    from app.api.deps import get_current_user
    from app.models.connection import DBConnection

    # Resolve caller from JWT (best-effort)
    caller_email: Optional[str] = None
    caller_role: str = "viewer"
    try:
        if request is not None:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                from app.services.auth_service import AuthService
                payload = AuthService.verify_token(token)
                if payload:
                    caller_email = payload.get("sub")
                    caller_role = payload.get("role", "viewer")
    except Exception:
        pass

    if not caller_email:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Authentication required")

    # Authorise: admin/analyst may scan anything; viewer only their own connections
    conn = db.query(DBConnection).filter(
        DBConnection.id == connection_id,
        DBConnection.is_deleted == False,  # noqa: E712
    ).first()
    if not conn:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Connection not found")

    is_privileged = caller_role in ("admin", "analyst")
    is_owner = conn.owner_email == caller_email or conn.owner_email is None
    if not is_privileged and not is_owner:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403,
            detail="You can only scan connections you own. Ask an admin to grant you analyst access.",
        )

    result = SchemaCatalogService.scan_connection(db, connection_id, actor=caller_email)
    return ScanResult(**result)


@router.get("/{connection_id}/tables", response_model=CatalogTableListResponse)
def list_catalog_tables(
    connection_id: int,
    q: Optional[str] = Query(None, description="Search table/column names (Task #4, FR4)"),
    data_type: Optional[str] = Query(None, description="Filter by exact column data type"),
    classification_label: Optional[str] = Query(
        None, pattern="^(PII|Sensitive|Public)$",
        description="Filter by classification label",
    ),
    db: Session = Depends(get_db),
):
    tables: List[CatalogTable] = SchemaCatalogService.get_catalog(
        db, connection_id, q=q, data_type=data_type, classification_label=classification_label,
    )
    return CatalogTableListResponse(
        connection_id=connection_id,
        tables=[_table_response(t) for t in tables],
    )


# ── Task #2: profiling ──────────────────────────────────────────────

@router.post("/{connection_id}/profile", response_model=ProfileEnqueueResult, status_code=202)
def profile_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    """Enqueue profiling for every table discovered in this connection's
    catalog. Requires a prior scan (POST /scan/{connection_id})."""
    from app.models.connection import DBConnection

    conn = db.query(DBConnection).filter(DBConnection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="connection not found")

    table_count = db.query(CatalogTable).filter(CatalogTable.connection_id == connection_id).count()
    if table_count == 0:
        raise HTTPException(
            status_code=400,
            detail="no catalog tables found; run a scan (POST /scan/{connection_id}) first",
        )

    from app.tasks.schema_intel_tasks import profile_connection_task
    task = profile_connection_task.delay(connection_id)

    from app.services.audit_helper import record_audit
    record_audit(
        db, "profiling_started", actor=user.email,
        connection_id=connection_id, connection_name=conn.name,
        payload={"task_id": task.id, "tables": table_count},
    )
    db.commit()

    return ProfileEnqueueResult(
        status="queued", task_id=task.id,
        message=f"Profiling {table_count} table(s). Poll GET /api/v1/tasks/{{task_id}} for status.",
    )


# ── Task #7: manual classification override ──────────────────────────

@router.put("/columns/{column_id}/classification", response_model=ColumnClassificationResponse)
def override_classification(
    column_id: int,
    req: ClassificationOverrideRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    row = SchemaCatalogService.override_classification(
        db, column_id, label=req.label, level=req.level, actor=user.email,
    )
    return ColumnClassificationResponse.model_validate(row)
