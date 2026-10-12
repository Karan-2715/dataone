import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.config import settings
from app.core.database import get_db
from app.models.audit import AuditLog
from app.models.connection import DBConnection
from app.models.saved_query import SavedQuery
from app.models.user import User
from app.schemas.query_studio import (
    QueryExecuteRequest,
    QueryExecuteResponse,
    QueryHistoryEntry,
    QueryHistoryResponse,
    SavedQueryCreate,
    SavedQueryResponse,
)
from app.services import query_execution_service
from app.services.audit_helper import emit_audit_event
from app.services.connection_service import ConnectionService

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_connection(db: Session, connection_id: int, owner_email: str | None = None) -> DBConnection:
    """Resolve a connection by ID, scoped to the caller's ownership.
    
    `owner_email=None` means unscoped (admin bypass); a real value 404s
    rows owned by someone else rather than leaking their existence via 403.
    """
    return ConnectionService.get_connection(db, connection_id, owner_email=owner_email)


def _audit_execution(db: Session, user: User, conn: DBConnection, sql: str, result: dict) -> None:
    if result.get("error"):
        event_type, outcome = "query.error", "failure"
    elif result.get("requires_confirmation"):
        event_type, outcome = "query.blocked", "warning"
    elif result["statement_type"] in ("insert", "update", "delete", "ddl"):
        event_type, outcome = "query.write_executed", "success"
    else:
        event_type, outcome = "query.select_executed", "success"

    emit_audit_event(
        db,
        event_type=event_type,
        actor=user.email,
        module="query_studio",
        target_type="connection",
        target_id=conn.id,
        target_name=conn.name,
        summary=sql[:200],
        outcome=outcome,
        duration_ms=result.get("duration_ms"),
        metadata={
            "sql": sql,
            "statement_type": result["statement_type"],
            "tables_referenced": result["tables_referenced"],
            "row_count": result.get("row_count"),
            "affected_rows": result.get("affected_rows"),
            "error": result.get("error"),
        },
    )


@router.post("/execute", response_model=QueryExecuteResponse)
def execute_query(
    req: QueryExecuteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    """Execute raw SQL against a connection (QS-T1/T2/T3).

    SELECT executes and returns a paginated result. INSERT/UPDATE/DELETE/DDL
    require the admin role and req.confirm=true — without that, the
    statement is classified and returned with requires_confirmation=true,
    nothing is executed.
    """
    owner_email = None if user.role == "admin" else user.email
    conn = _get_connection(db, req.connection_id, owner_email=owner_email)
    result = query_execution_service.execute(
        conn, req.sql, role=user.role, page=req.page, page_size=req.page_size, confirm=req.confirm,
    )
    _audit_execution(db, user, conn, req.sql, result)
    db.commit()
    return QueryExecuteResponse(**result)


@router.get("/history", response_model=QueryHistoryResponse)
def get_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    """Per-user query execution history (QS-T6), sourced from the audit log."""
    q = (
        db.query(AuditLog)
        .filter(AuditLog.module == "query_studio")
        .filter(AuditLog.actor == user.email)
        .filter(AuditLog.event_type.in_(["query.select_executed", "query.write_executed", "query.error"]))
    )
    total = q.count()
    rows = (
        q.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    history = [
        QueryHistoryEntry(
            id=r.id,
            actor=r.actor,
            sql=(r.event_metadata or {}).get("sql"),
            connection_id=r.target_id,
            statement_type=(r.event_metadata or {}).get("statement_type"),
            outcome=r.outcome,
            row_count=(r.event_metadata or {}).get("row_count"),
            duration_ms=r.duration_ms,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return QueryHistoryResponse(history=history, total=total, page=page, page_size=page_size)


@router.post("/export")
def export_results(
    req: QueryExecuteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    """Re-run a SELECT and stream the (bounded) result set as CSV (QS-T5).

    Read-only by design — export never executes a write/DDL statement, even
    with confirm=true. The connector layer fetches the full result set into
    memory regardless (no server-side cursor support — see
    query_execution_service's docstring), so this exports whatever fits
    within settings.QUERY_STUDIO_MAX_RESULT_ROWS, same as the paginated view.
    """
    owner_email = None if user.role == "admin" else user.email
    conn = _get_connection(db, req.connection_id, owner_email=owner_email)
    result = query_execution_service.execute(
        conn, req.sql, role=user.role, page=1,
        page_size=settings.QUERY_STUDIO_MAX_RESULT_ROWS,
        confirm=False,
    )
    if result["statement_type"] != "select":
        raise HTTPException(status_code=400, detail="Export only supports SELECT statements")
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(result["columns"])
    for row in result["rows"]:
        writer.writerow([row.get(c, "") for c in result["columns"]])
    output.seek(0)

    _audit_execution(db, user, conn, req.sql, result)
    db.commit()

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="query_studio_export.csv"'},
    )


@router.post("/saved", response_model=SavedQueryResponse)
def create_saved_query(
    req: SavedQueryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    owner_email = None if user.role == "admin" else user.email
    _get_connection(db, req.connection_id, owner_email=owner_email)
    saved = SavedQuery(
        connection_id=req.connection_id, name=req.name, sql_text=req.sql_text, created_by=user.email,
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)
    return saved


@router.get("/saved", response_model=list[SavedQueryResponse])
def list_saved_queries(
    connection_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    q = db.query(SavedQuery).filter(SavedQuery.created_by == user.email)
    if connection_id is not None:
        q = q.filter(SavedQuery.connection_id == connection_id)
    return q.order_by(SavedQuery.created_at.desc()).all()


@router.delete("/saved/{saved_id}", status_code=204)
def delete_saved_query(
    saved_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    saved = db.query(SavedQuery).filter(SavedQuery.id == saved_id).first()
    if not saved:
        raise HTTPException(status_code=404, detail="Saved query not found")
    if saved.created_by != user.email and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your saved query")
    db.delete(saved)
    db.commit()
