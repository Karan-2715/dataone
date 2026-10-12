"""Schema Mapper — versioned, audited, role-gated mapping workspace API."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.mapping import AISuggestion, FieldMapping, Mapping, MappingVersion, MappingRun
from app.models.user import User
from app.schemas.mapping import (
    AnnotationCreate, AnnotationResponse, EdgeCreate, EdgeResponse, EdgeTransformationUpdate,
    FeedbackSummaryResponse, MappingCreate,
    MappingListResponse, MappingResponse, MappingUpdate, PublishResponse,
    ReportResponse, ReviewQueueResponse, ReviewTransitionRequest, SourceRef,
    SuggestionAcceptRequest, SuggestionListResponse, SuggestionResponse, TargetRef,
    TransformationPreviewResponse, ValidationIssue, ValidationResponse,
    VersionDiffResponse, VersionListResponse, VersionSummaryResponse,
)
from app.services.mapping_annotation_service import MappingAnnotationService
from app.services.mapping_feedback_service import FeedbackSummaryService
from app.services.mapping_report_service import MappingDocumentationService, MigrationReportService
from app.services.mapping_review_service import MappingReviewService
from app.services.mapping_service import MappingService
from app.services.transformation_preview_service import TransformationPreviewService

logger = logging.getLogger(__name__)
router = APIRouter()


def _edge_response(edge: FieldMapping) -> EdgeResponse:
    return EdgeResponse(
        id=edge.id,
        mapping_id=edge.mapping_id,
        target=TargetRef(
            table=edge.target_table, column=edge.target_column,
            type=edge.target_type,
            nullable=(
                bool(edge.target_nullable)
                if edge.target_nullable is not None else None
            ),
            primary_key=bool(edge.target_is_pk),
        ),
        sources=[SourceRef(**s) for s in (edge.sources or [])],
        transformation=edge.transformation or {"kind": "direct"},
        origin=edge.origin,
        ai_confidence=edge.ai_confidence,
        audit=edge.audit or {},
        created_at=edge.created_at,
        updated_at=edge.updated_at,
    )


def _mapping_response(m: Mapping) -> MappingResponse:
    latest_run_id = None
    latest_run_state = None
    if getattr(m, "runs", None):
        latest = sorted(m.runs, key=lambda x: x.id)[-1]
        latest_run_id = latest.id
        latest_run_state = latest.state

    return MappingResponse(
        id=m.id,
        name=m.name,
        source_id=m.source_id,
        target_id=m.target_id,
        status=m.status,
        review_stage=m.review_stage,
        current_version_id=m.current_version_id,
        created_by=m.created_by,
        created_at=m.created_at,
        updated_at=m.updated_at,
        latest_run_id=latest_run_id,
        latest_run_state=latest_run_state,
        edges=[
            _edge_response(e) for e in (m.edges or []) if e.version_id is None
        ],
    )


@router.post("/", response_model=MappingResponse, status_code=201)
def create_mapping(
    req: MappingCreate, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    m = MappingService.create_mapping(
        db, source_id=req.source_id, target_id=req.target_id,
        name=req.name, actor=user.email,
    )
    return _mapping_response(m)


@router.get("/", response_model=MappingListResponse)
def list_mappings(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Review §11.8: paginate to support the NFR of ≥10,000 mappings per
    # tenant. Returns {items, total, limit, offset, has_more} instead of a
    # bare list so the frontend can render "Load more" / page indicators.
    items, total = MappingService.list_mappings(
        db, limit=limit, offset=offset,
    )
    return {
        "items": [_mapping_response(m) for m in items],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(items)) < total,
    }


@router.get("/review-queue", response_model=ReviewQueueResponse)
def get_review_queue(
    stage: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    """E13 — mappings awaiting review action. Registered before
    /{mapping_id} so 'review-queue' is never mistaken for a mapping id."""
    items = MappingReviewService.get_queue(db, stage=stage)
    return {"items": [_mapping_response(m) for m in items], "total": len(items)}


@router.get("/feedback-summary", response_model=FeedbackSummaryResponse)
def get_feedback_summary(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """E14-5 — acceptance-rate + most-corrected suggestion pairs, computed
    live from AISuggestion outcomes across every mapping. Registered
    before /{mapping_id} so 'feedback-summary' is never mistaken for a
    mapping id."""
    return FeedbackSummaryService.get_summary(db)


@router.get("/{mapping_id}", response_model=MappingResponse)
def get_mapping(
    mapping_id: int, db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    m = MappingService.get_mapping(db, mapping_id)
    return _mapping_response(m)


@router.put("/{mapping_id}", response_model=MappingResponse)
def update_mapping(
    mapping_id: int, req: MappingUpdate, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    m = MappingService.update_mapping_meta(
        db, mapping_id, name=req.name, actor=user.email,
    )
    return _mapping_response(m)


@router.delete("/{mapping_id}", status_code=204)
def delete_mapping(
    mapping_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    MappingService.delete_mapping(db, mapping_id, actor=user.email)
    return None


@router.post("/{mapping_id}/edges", response_model=EdgeResponse, status_code=201)
def add_edge(
    mapping_id: int, req: EdgeCreate, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    edge = MappingService.add_edge(
        db, mapping_id,
        target=req.target.model_dump(exclude_none=True),
        sources=[s.model_dump(exclude_none=True) for s in req.sources],
        transformation=req.transformation,
        origin=req.origin,
        actor=user.email,
    )
    return _edge_response(edge)


@router.delete("/{mapping_id}/edges/{edge_id}", status_code=204)
def remove_edge(
    mapping_id: int, edge_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    MappingService.remove_edge(db, mapping_id, edge_id, actor=user.email)
    return None


@router.get("/{mapping_id}/edges/{edge_id}/preview", response_model=TransformationPreviewResponse)
def preview_edge_transformation(
    mapping_id: int, edge_id: int, db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Enterprise v2, E10-7 — 'source value -> transformation -> output'
    on a few real sample rows. Read-only; see transformation_preview_service
    for why this evaluates in Python rather than compiling to SQL."""
    return TransformationPreviewService.get_preview(db, mapping_id, edge_id)


@router.put(
    "/{mapping_id}/edges/{edge_id}/transformation",
    response_model=EdgeResponse,
)
def update_edge_transformation(
    mapping_id: int, edge_id: int,
    req: EdgeTransformationUpdate, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    edge = MappingService.update_edge_transformation(
        db, mapping_id, edge_id, req.transformation, actor=user.email,
    )
    return _edge_response(edge)


@router.post("/{mapping_id}/suggestions")
def request_suggestions(
    mapping_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    task_id = MappingService.request_suggestions(
        db, mapping_id, actor=user.email,
    )
    return {"task_id": task_id, "status": "PENDING", "mapping_id": mapping_id}


@router.get("/{mapping_id}/suggestions", response_model=SuggestionListResponse)
def list_suggestions(
    mapping_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Review §11.8: paginate. Verify the mapping exists (so 404 fires
    # cleanly when the id is wrong instead of returning an empty list).
    MappingService.get_mapping(db, mapping_id)
    base = db.query(AISuggestion).filter(AISuggestion.mapping_id == mapping_id)
    total = base.count()
    rows = (
        base.order_by(AISuggestion.confidence.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(rows)) < total,
    }


@router.post(
    "/{mapping_id}/suggestions/{suggestion_id}/accept",
    response_model=EdgeResponse,
)
def accept_suggestion(
    mapping_id: int, suggestion_id: int,
    req: SuggestionAcceptRequest, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    edge = MappingService.accept_suggestion(
        db, mapping_id, suggestion_id, req.transformation, actor=user.email,
    )
    return _edge_response(edge)


@router.post(
    "/{mapping_id}/suggestions/{suggestion_id}/reject",
    response_model=SuggestionResponse,
)
def reject_suggestion(
    mapping_id: int, suggestion_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    sug = MappingService.reject_suggestion(
        db, mapping_id, suggestion_id, actor=user.email,
    )
    return sug


@router.post("/{mapping_id}/validate", response_model=ValidationResponse)
def validate_mapping(
    mapping_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    summary = MappingService.validate(db, mapping_id, actor=user.email)
    return ValidationResponse(
        mapping_id=summary["mapping_id"],
        ok_count=summary["ok_count"],
        warning_count=summary["warning_count"],
        blocking_count=summary["blocking_count"],
        issues=[ValidationIssue(**i) for i in summary["issues"]],
    )


@router.post("/{mapping_id}/publish", response_model=PublishResponse)
def publish_mapping(
    mapping_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    v = MappingService.publish(db, mapping_id, actor=user.email)
    return PublishResponse(
        mapping_id=mapping_id,
        version_number=v.version_number,
        version_id=v.id,
        status=v.status,
        published_at=v.published_at,
        published_by=v.published_by,
    )


def _version_summary(v: MappingVersion, *, current_version_id: Optional[int]) -> VersionSummaryResponse:
    return VersionSummaryResponse(
        id=v.id,
        version_number=v.version_number,
        status=v.status,
        published_at=v.published_at,
        published_by=v.published_by,
        edge_count=len(v.edges_snapshot or []),
        is_current=(v.id == current_version_id),
    )


@router.get("/{mapping_id}/versions", response_model=VersionListResponse)
def list_versions(
    mapping_id: int, db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """E13-5 — every published version of this mapping, newest first."""
    m = MappingService.get_mapping(db, mapping_id)
    versions = MappingService.list_versions(db, mapping_id)
    return {
        "items": [_version_summary(v, current_version_id=m.current_version_id) for v in versions],
    }


@router.get("/{mapping_id}/versions/diff", response_model=VersionDiffResponse)
def diff_versions(
    mapping_id: int,
    from_version_id: int = Query(...),
    to_version_id: int = Query(...),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """E13-5 — field-level diff (added/removed/changed) between two
    immutable version snapshots of this mapping."""
    m = MappingService.get_mapping(db, mapping_id)
    result = MappingService.diff_versions(db, mapping_id, from_version_id, to_version_id)
    result["from_version"] = _version_summary(result["from_version"], current_version_id=m.current_version_id)
    result["to_version"] = _version_summary(result["to_version"], current_version_id=m.current_version_id)
    return result


@router.post("/{mapping_id}/versions/{version_id}/rollback", response_model=MappingResponse)
def rollback_to_version(
    mapping_id: int, version_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    """E13-6 / uiux 'createDraftVersion' — reopen a published mapping for
    editing from a given version's snapshot (the current version for
    'Revise', an older one for a true rollback). Never rewrites history;
    always creates fresh draft edges via the existing edit lifecycle."""
    m = MappingService.create_draft_revision(db, mapping_id, version_id, actor=user.email)
    return _mapping_response(m)


@router.post("/{mapping_id}/review/transition", response_model=MappingResponse)
def transition_review_stage(
    mapping_id: int, req: ReviewTransitionRequest, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """E13 — advance (or reject/resubmit) a mapping's governance review
    stage. Independent of status/publish; see mapping_review_service.
    An optional E16-3 note becomes a steward comment on this transition."""
    m = MappingReviewService.transition(
        db, mapping_id, req.to_stage, actor=user.email, role=user.role, note=req.note,
    )
    return _mapping_response(m)


@router.post("/{mapping_id}/annotations", response_model=AnnotationResponse, status_code=201)
def create_annotation(
    mapping_id: int, payload: AnnotationCreate, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """E16-1/2 — pin an async comment to a mapping or one of its edges."""
    return MappingAnnotationService.add_comment(
        db, mapping_id, author=user.email, body=payload.body,
        edge_id=payload.edge_id, parent_id=payload.parent_id,
    )


@router.get("/{mapping_id}/annotations", response_model=List[AnnotationResponse])
def list_annotations(
    mapping_id: int, db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """E16-1/2 — every non-deleted comment/steward-comment on a mapping,
    oldest first; the client reconstructs threads from parent_id."""
    return MappingAnnotationService.list_for_mapping(db, mapping_id)


@router.delete("/{mapping_id}/annotations/{annotation_id}", status_code=204)
def delete_annotation(
    mapping_id: int, annotation_id: int, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """E16-1/2 — soft-delete; only the comment's author or an admin may."""
    MappingAnnotationService.soft_delete(db, mapping_id, annotation_id, actor=user.email, role=user.role)


@router.get("/{mapping_id}/export")
def export_mapping(
    mapping_id: int,
    version_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    artifact = MappingService.export_json(
        db, mapping_id, actor=_user.email, version_id=version_id,
    )
    return artifact


@router.get("/{mapping_id}/documentation", response_model=ReportResponse)
def get_mapping_documentation(
    mapping_id: int, db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """E15 — a readable Markdown spec of the mapping (fields, transformations,
    review stage, validation) composed entirely from existing data."""
    return MappingDocumentationService.generate(db, mapping_id)


@router.get("/{mapping_id}/migration-report", response_model=ReportResponse)
def get_migration_report(
    mapping_id: int, db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """E15 — readiness summary composing validation, live schema diff, and
    related risk findings; a degraded section is marked "not available",
    never fabricated."""
    return MigrationReportService.generate(db, mapping_id)


from app.services.migration_service import MigrationService
from app.schemas.mapping import MappingRunResponse, RunStatusUpdate
from pydantic import BaseModel

from typing import Any, Dict, List, Optional
class MappingRunCreate(BaseModel):
    transformation_code: Optional[str] = None
    patches: Optional[List[Dict[str, Any]]] = None

@router.post("/{mapping_id}/runs", response_model=MappingRunResponse, status_code=201)
def trigger_migration_run(
    mapping_id: int, 
    req: Optional[MappingRunCreate] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "analyst")),
):
    """E13 — Trigger a databricks job to run the migration for this mapping."""
    # Ensure mapping exists
    MappingService.get_mapping(db, mapping_id)
    transformation_code = req.transformation_code if req else None
    patches = req.patches if req else None
    run = MigrationService.trigger_migration(db, mapping_id, user.email, transformation_code, patches)
    return run


@router.get("/{mapping_id}/runs/{run_id}", response_model=MappingRunResponse)
def get_migration_run(
    mapping_id: int, run_id: int, db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    from fastapi import HTTPException
    run = db.query(MappingRun).filter(MappingRun.id == run_id, MappingRun.mapping_id == mapping_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post("/{mapping_id}/runs/{run_id}/status", response_model=MappingRunResponse)
def update_migration_run_status(
    mapping_id: int, run_id: int, payload: RunStatusUpdate,
    request: Request,
    db: Session = Depends(get_db)
):
    """Callback for the Databricks notebook to report status. Uses X-Run-Token auth."""
    token = request.headers.get("X-Run-Token", "").strip()
    if not token:
        token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        
    run = db.query(MappingRun).filter(MappingRun.id == run_id, MappingRun.mapping_id == mapping_id).first()
    from fastapi import HTTPException
    if not run or run.run_token != token:
        raise HTTPException(status_code=401, detail="Invalid run token")
        
    run.state = payload.state
    if payload.rows_read is not None: run.rows_read = payload.rows_read
    if payload.rows_written is not None: run.rows_written = payload.rows_written
    if payload.error is not None: run.error = payload.error
    if payload.state in ["succeeded", "failed"]:
        from datetime import datetime
        run.finished_at = datetime.utcnow()
        
    db.commit()
    db.refresh(run)
    return run

@router.get("/{mapping_id}/runs/{run_id}/export")
def export_run_mapping(
    mapping_id: int, run_id: int, request: Request,
    db: Session = Depends(get_db)
):
    """Special endpoint for Databricks runner to fetch mapping config using run_token."""
    token = request.headers.get("X-Run-Token", "").strip()
    if not token:
        # Fallback to Authorization for backwards compatibility just in case
        token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        
    run = db.query(MappingRun).filter(MappingRun.id == run_id, MappingRun.mapping_id == mapping_id).first()
    from fastapi import HTTPException
    if not run or run.run_token != token:
        raise HTTPException(status_code=401, detail="Invalid run token")
        
    return MappingService.export_json(db, mapping_id, actor="system_runner")
