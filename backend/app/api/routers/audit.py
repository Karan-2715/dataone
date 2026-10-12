import csv
import io
import json
import logging
from datetime import datetime
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.audit import AuditLog
from app.schemas.audit import (
    AuditEventBatchRequest,
    AuditEventBatchResponse,
    AuditEventCreate,
    AuditEventResponse,
    AuditFacets,
    AuditSearchResponse,
    IntegrityVerificationResult,
    RetentionStatus,
)
from app.services.audit_helper import (
    AuditBufferFullError,
    ingest_audit_event_durable,
    verify_hash_chain,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Allow-list for `sort_by` (AUDIT-T4) — `getattr(AuditLog, sort_by)` on an
# unvalidated query param would let a caller sort by any attribute of the
# class, including non-column ones, which can 500 the request.
_SORTABLE_COLUMNS = {
    "created_at": AuditLog.created_at,
    "sequence": AuditLog.sequence,
    "event_type": AuditLog.event_type,
    "actor": AuditLog.actor,
    "module": AuditLog.module,
    "outcome": AuditLog.outcome,
    "duration_ms": AuditLog.duration_ms,
}


# ── Ingestion ─────────────────────────────────────────────────────────────


@router.post("/events", response_model=AuditEventBatchResponse)
def ingest_events(batch: AuditEventBatchRequest, db: Session = Depends(get_db)):
    """Batch ingestion of audit events (AUDIT-T2).

    Accepts up to settings.AUDIT_INGEST_BATCH_MAX events per request. Each
    event is validated and written individually; a DB write failure falls
    back to the durable buffer (see app.core.audit_buffer) instead of losing
    the event, so it still counts as accepted. Only when that fallback
    buffer is also full does an event get rejected as backpressure — and if
    every event in the batch hits that, the whole request is rejected with
    503 so the caller retries the batch later rather than losing part of it
    silently.
    """
    accepted = 0
    rejected = 0
    buffer_full_count = 0
    errors = []

    for event in batch.events:
        try:
            ingest_audit_event_durable(
                db=db,
                event_type=event.event_type,
                actor=event.actor,
                module=event.module,
                target_type=event.target_type,
                target_id=event.target_id,
                target_name=event.target_name,
                before=event.before,
                after=event.after,
                correlation_id=event.correlation_id,
                outcome=event.outcome,
                summary=event.summary,
                duration_ms=event.duration_ms,
                metadata=event.metadata,
                connection_id=event.connection_id,
                connection_name=event.connection_name,
                payload=event.payload,
                status=event.status,
            )
            accepted += 1
        except AuditBufferFullError as exc:
            rejected += 1
            buffer_full_count += 1
            errors.append({"event_type": event.event_type, "error": str(exc)})
        except Exception as exc:
            rejected += 1
            errors.append({"event_type": event.event_type, "error": str(exc)})

    db.commit()

    if accepted == 0 and buffer_full_count == len(batch.events) and buffer_full_count > 0:
        raise HTTPException(
            status_code=503,
            detail="Audit ingestion is backpressured — durability buffer is full",
            headers={"Retry-After": "5"},
        )

    return AuditEventBatchResponse(
        accepted=accepted,
        rejected=rejected,
        errors=errors,
    )


# ── Search / Filter ───────────────────────────────────────────────────────


@router.get("/events", response_model=AuditSearchResponse)
def search_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    actor: Optional[str] = Query(None),
    module: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    target_id: Optional[str] = Query(None),
    correlation_id: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Search/filter audit events (AUDIT-T4).

    Supports filtering by actor, module, event_type, target, correlation_id,
    outcome, and date range. Full-text search on summary and event_type.

    When correlation_id is given and the caller didn't request a specific
    sort, results default to sequence-ascending — the order a correlation
    trace actually happened in, not most-recent-first.
    """
    q = db.query(AuditLog)

    if actor:
        q = q.filter(AuditLog.actor == actor)
    if module:
        q = q.filter(AuditLog.module == module)
    if event_type:
        q = q.filter(AuditLog.event_type == event_type)
    if target_type:
        q = q.filter(AuditLog.target_type == target_type)
    if target_id:
        q = q.filter(AuditLog.target_id == target_id)
    if correlation_id:
        q = q.filter(AuditLog.correlation_id == correlation_id)
    if outcome:
        q = q.filter(AuditLog.outcome == outcome)
    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from)
            q = q.filter(AuditLog.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
            q = q.filter(AuditLog.created_at <= dt_to)
        except ValueError:
            pass
    if search:
        search_term = f"%{search}%"
        q = q.filter(
            or_(
                AuditLog.summary.ilike(search_term),
                AuditLog.event_type.ilike(search_term),
                AuditLog.actor.ilike(search_term),
            )
        )

    # Total count before pagination
    total = q.count()

    # Sorting — correlation_id implies chain tracing, defaults to
    # sequence-ascending unless the caller overrides it.
    effective_sort_by = sort_by or ("sequence" if correlation_id else "created_at")
    effective_sort_order = sort_order or ("asc" if correlation_id else "desc")
    sort_col = _SORTABLE_COLUMNS.get(effective_sort_by, AuditLog.created_at)
    if effective_sort_order == "asc":
        q = q.order_by(sort_col.asc())
    else:
        q = q.order_by(sort_col.desc())

    # Pagination
    offset = (page - 1) * page_size
    events = q.offset(offset).limit(page_size).all()

    # Compute facets
    facets = _compute_facets(db, actor, module, event_type, outcome)

    return AuditSearchResponse(
        events=[AuditEventResponse.model_validate(e) for e in events],
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + page_size) < total,
        facets=facets,
    )


@router.get("/events/facets")
def get_facets(
    db: Session = Depends(get_db),
):
    """Return faceted search aggregates (AUDIT-T4)."""
    return _compute_facets(db)


def _compute_facets(
    db: Session,
    actor_filter: Optional[str] = None,
    module_filter: Optional[str] = None,
    event_type_filter: Optional[str] = None,
    outcome_filter: Optional[str] = None,
) -> AuditFacets:
    """Compute faceted search aggregates."""
    # Module counts
    module_q = db.query(AuditLog.module, func.count(AuditLog.id))
    if actor_filter:
        module_q = module_q.filter(AuditLog.actor == actor_filter)
    module_q = module_q.filter(AuditLog.module.isnot(None))
    module_q = module_q.group_by(AuditLog.module).order_by(func.count(AuditLog.id).desc()).limit(20)
    modules = {row[0] or "unknown": row[1] for row in module_q.all()}

    # Event type counts
    type_q = db.query(AuditLog.event_type, func.count(AuditLog.id))
    if actor_filter:
        type_q = type_q.filter(AuditLog.actor == actor_filter)
    if module_filter:
        type_q = type_q.filter(AuditLog.module == module_filter)
    type_q = type_q.group_by(AuditLog.event_type).order_by(func.count(AuditLog.id).desc()).limit(50)
    event_types = {row[0]: row[1] for row in type_q.all()}

    # Outcome counts
    outcome_q = db.query(AuditLog.outcome, func.count(AuditLog.id))
    if actor_filter:
        outcome_q = outcome_q.filter(AuditLog.actor == actor_filter)
    if module_filter:
        outcome_q = outcome_q.filter(AuditLog.module == module_filter)
    if event_type_filter:
        outcome_q = outcome_q.filter(AuditLog.event_type == event_type_filter)
    outcome_q = outcome_q.group_by(AuditLog.outcome)
    outcomes = {row[0]: row[1] for row in outcome_q.all()}

    # Actor counts (top 20)
    actor_q = db.query(AuditLog.actor, func.count(AuditLog.id))
    if module_filter:
        actor_q = actor_q.filter(AuditLog.module == module_filter)
    actor_q = actor_q.group_by(AuditLog.actor).order_by(func.count(AuditLog.id).desc()).limit(20)
    actors = {row[0]: row[1] for row in actor_q.all()}

    # Date range
    date_q = db.query(
        func.min(AuditLog.created_at),
        func.max(AuditLog.created_at),
    )
    date_result = date_q.first()
    date_range = None
    if date_result and date_result[0] and date_result[1]:
        date_range = {
            "earliest": date_result[0].isoformat() if date_result[0] else None,
            "latest": date_result[1].isoformat() if date_result[1] else None,
        }

    return AuditFacets(
        modules=modules,
        event_types=event_types,
        outcomes=outcomes,
        actors=actors,
        date_range=date_range,
    )


# ── Single event ──────────────────────────────────────────────────────────


@router.get("/events/{event_id}", response_model=AuditEventResponse)
def get_event(event_id: int, db: Session = Depends(get_db)):
    """Get a single audit event by ID."""
    event = db.query(AuditLog).filter(AuditLog.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Audit event not found")
    return event


# ── Integrity verification (AUDIT-T3) ─────────────────────────────────────


@router.post("/verify", response_model=IntegrityVerificationResult)
def verify_integrity(db: Session = Depends(get_db)):
    """Verify the hash chain integrity of the audit log (AUDIT-T3).

    Walks the chain from the first event and recomputes hashes.
    Reports any tampered events or chain breaks.
    """
    result = verify_hash_chain(db)
    return IntegrityVerificationResult(**result)


# ── Export (AUDIT-T6) ─────────────────────────────────────────────────────

_EXPORT_CSV_HEADERS = [
    "id", "event_type", "actor", "module", "target_type", "target_id",
    "target_name", "correlation_id", "outcome", "summary",
    "duration_ms", "sequence", "created_at",
]


def _apply_audit_filters(
    q,
    actor: Optional[str] = None,
    module: Optional[str] = None,
    event_type: Optional[str] = None,
    target_type: Optional[str] = None,
    correlation_id: Optional[str] = None,
    outcome: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
):
    """Shared filter logic for search (AUDIT-T4) and export (AUDIT-T6) — the
    task spec requires export to accept the same filter parameters."""
    if actor:
        q = q.filter(AuditLog.actor == actor)
    if module:
        q = q.filter(AuditLog.module == module)
    if event_type:
        q = q.filter(AuditLog.event_type == event_type)
    if target_type:
        q = q.filter(AuditLog.target_type == target_type)
    if correlation_id:
        q = q.filter(AuditLog.correlation_id == correlation_id)
    if outcome:
        q = q.filter(AuditLog.outcome == outcome)
    if date_from:
        try:
            q = q.filter(AuditLog.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(AuditLog.created_at <= datetime.fromisoformat(date_to))
        except ValueError:
            pass
    if search:
        q = q.filter(AuditLog.summary.ilike(f"%{search}%"))
    return q


def _stream_export_rows(
    actor, module, event_type, target_type, correlation_id, outcome,
    date_from, date_to, search,
):
    """Yield matching AuditLog rows from a dedicated session, batched via
    yield_per so the full result set is never materialized in memory.

    Opens its own session rather than reusing the request's `Depends(get_db)`
    session: that session is closed by FastAPI as soon as the endpoint
    function returns, which happens as soon as the StreamingResponse is
    constructed — *before* this generator (which runs afterwards, as the
    response body streams) gets to iterate it. Using a separate session
    scoped to this generator's own lifetime avoids querying through an
    already-closed session mid-stream.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        q = db.query(AuditLog)
        q = _apply_audit_filters(
            q, actor, module, event_type, target_type, correlation_id,
            outcome, date_from, date_to, search,
        )
        q = (
            q.order_by(AuditLog.created_at.desc())
            .limit(settings.AUDIT_EXPORT_MAX_ROWS)
            .execution_options(stream_results=True)  # real server-side cursor on Postgres
        )
        for row in q.yield_per(1000):
            yield row
    finally:
        db.close()


def _stream_csv(rows) -> Iterator[str]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_EXPORT_CSV_HEADERS)
    yield buf.getvalue()

    for e in rows:
        buf.seek(0)
        buf.truncate(0)
        writer.writerow([
            e.id, e.event_type, e.actor, e.module or "", e.target_type or "",
            e.target_id or "", e.target_name or "", e.correlation_id or "",
            e.outcome, e.summary or "", e.duration_ms or "", e.sequence or "",
            e.created_at.isoformat() if e.created_at else "",
        ])
        yield buf.getvalue()


def _stream_ndjson(rows) -> Iterator[str]:
    for e in rows:
        yield json.dumps({
            "id": e.id,
            "event_type": e.event_type,
            "actor": e.actor,
            "module": e.module,
            "target_type": e.target_type,
            "target_id": e.target_id,
            "target_name": e.target_name,
            "correlation_id": e.correlation_id,
            "outcome": e.outcome,
            "summary": e.summary,
            "duration_ms": e.duration_ms,
            "sequence": e.sequence,
            "event_hash": e.event_hash,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }) + "\n"


@router.get("/export")
def export_events(
    format: str = Query("csv", regex="^(csv|json)$"),
    actor: Optional[str] = Query(None),
    module: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    correlation_id: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    """Export filtered audit events as CSV or JSON (AUDIT-T6).

    Uses the same filter parameters as the search endpoint. Streams rows
    from the DB via yield_per instead of loading the full result set into
    memory, capped at settings.AUDIT_EXPORT_MAX_ROWS.
    """
    rows = _stream_export_rows(
        actor, module, event_type, target_type, correlation_id, outcome,
        date_from, date_to, search,
    )
    today = datetime.utcnow().strftime("%Y-%m-%d")

    if format == "csv":
        return StreamingResponse(
            _stream_csv(rows),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="audit_export_{today}.csv"'},
        )
    return StreamingResponse(
        _stream_ndjson(rows),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="audit_export_{today}.jsonl"'},
    )


# ── Retention status (AUDIT-T7) ───────────────────────────────────────────


@router.get("/retention-status", response_model=RetentionStatus)
def get_retention_status(db: Session = Depends(get_db)):
    """Get current retention policy status (AUDIT-T7)."""
    retention_days = settings.AUDIT_RETENTION_DAYS
    total = db.query(func.count(AuditLog.id)).scalar() or 0

    cutoff = datetime.utcnow()
    expired = (
        db.query(func.count(AuditLog.id))
        .filter(AuditLog.created_at < cutoff)
        .scalar() or 0
    )

    return RetentionStatus(
        retention_days=retention_days,
        total_events=total,
        events_in_retention_window=total - expired,
        events_expired=expired,
        next_cleanup_at=None,
    )


# ── Legacy backward compat endpoints ──────────────────────────────────────


@router.get("/", response_model=list[AuditEventResponse])
def list_audit_events_legacy(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    event_type: Optional[str] = Query(None),
    connection_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Legacy paginated audit event list (backward compatible)."""
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if event_type:
        q = q.filter(AuditLog.event_type == event_type)
    if connection_id is not None:
        q = q.filter(AuditLog.connection_id == connection_id)
    if status:
        q = q.filter(AuditLog.status == status)
    events = q.offset((page - 1) * page_size).limit(page_size).all()
    return [AuditEventResponse.model_validate(e) for e in events]


@router.get("/summary")
def audit_summary_legacy(db: Session = Depends(get_db)):
    """Legacy aggregate summary (backward compatible)."""
    rows = (
        db.query(AuditLog.event_type, AuditLog.status, func.count(AuditLog.id))
        .group_by(AuditLog.event_type, AuditLog.status)
        .all()
    )
    total = db.query(func.count(AuditLog.id)).scalar() or 0
    by_type: dict = {}
    for event_type, status, count in rows:
        by_type.setdefault(event_type, {"total": 0, "success": 0, "failure": 0, "warning": 0})
        by_type[event_type]["total"] += count
        by_type[event_type][status] = by_type[event_type].get(status, 0) + count
    return {"total": total, "by_event_type": by_type}


@router.get("/{id}", response_model=AuditEventResponse)
def get_audit_event_legacy(id: int, db: Session = Depends(get_db)):
    """Legacy single event endpoint (backward compatible)."""
    event = db.query(AuditLog).filter(AuditLog.id == id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Audit event not found")
    return event