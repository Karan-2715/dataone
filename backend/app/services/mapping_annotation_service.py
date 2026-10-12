"""Async collaboration annotations/comments (Enterprise v2, E16-1/2/3).

Deliberately request/response only — no presence, no live co-editing, no
broadcast. Those (E16-4/5/6) need a realtime transport + security
architecture decision this service does not make; see the E16 spec.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.mapping import FieldMapping
from app.models.mapping_annotation import MappingAnnotation
from app.services.audit_helper import record_audit
from app.services.mapping_service import MappingService

logger = logging.getLogger(__name__)

MAX_BODY_LENGTH = 4000


class MappingAnnotationService:
    @staticmethod
    def add_comment(
        db: Session, mapping_id: int, author: str, body: str,
        edge_id: int | None = None, parent_id: int | None = None,
    ) -> MappingAnnotation:
        MappingService.get_mapping(db, mapping_id)  # 404s if missing

        body = body.strip()
        if not body:
            raise HTTPException(status_code=422, detail="comment body cannot be empty")
        if len(body) > MAX_BODY_LENGTH:
            raise HTTPException(status_code=422, detail=f"comment body exceeds {MAX_BODY_LENGTH} characters")

        if edge_id is not None:
            edge = (
                db.query(FieldMapping)
                .filter(FieldMapping.id == edge_id, FieldMapping.mapping_id == mapping_id)
                .first()
            )
            if edge is None:
                raise HTTPException(status_code=422, detail=f"edge {edge_id} does not belong to mapping {mapping_id}")

        if parent_id is not None:
            parent = (
                db.query(MappingAnnotation)
                .filter(MappingAnnotation.id == parent_id, MappingAnnotation.mapping_id == mapping_id)
                .first()
            )
            if parent is None:
                raise HTTPException(status_code=422, detail=f"parent comment {parent_id} does not belong to mapping {mapping_id}")

        annotation = MappingAnnotation(
            mapping_id=mapping_id, edge_id=edge_id, parent_id=parent_id,
            kind="comment", author=author, body=body,
        )
        db.add(annotation)
        db.flush()
        record_audit(
            db, event_type="mapping.annotation_created", actor=author,
            payload={"mapping_id": mapping_id, "annotation_id": annotation.id, "edge_id": edge_id},
        )
        db.commit()
        db.refresh(annotation)
        logger.info("[mapping_annotation] stage=created mapping_id=%s annotation_id=%s author=%s",
                    mapping_id, annotation.id, author)
        return annotation

    @staticmethod
    def add_steward_comment(
        db: Session, mapping_id: int, author: str, body: str, review_stage: str,
    ) -> MappingAnnotation | None:
        """Called only from MappingReviewService.transition — never from a
        public endpoint — so a comment's "steward_comment" label is always
        truthful about being tied to a real review-stage transition."""
        body = (body or "").strip()
        if not body:
            return None
        annotation = MappingAnnotation(
            mapping_id=mapping_id, kind="steward_comment", review_stage=review_stage,
            author=author, body=body[:MAX_BODY_LENGTH],
        )
        db.add(annotation)
        db.flush()
        logger.info("[mapping_annotation] stage=steward_comment_created mapping_id=%s review_stage=%s",
                    mapping_id, review_stage)
        return annotation

    @staticmethod
    def list_for_mapping(db: Session, mapping_id: int) -> list[MappingAnnotation]:
        MappingService.get_mapping(db, mapping_id)  # 404s if missing
        return (
            db.query(MappingAnnotation)
            .filter(MappingAnnotation.mapping_id == mapping_id, MappingAnnotation.deleted_at.is_(None))
            .order_by(MappingAnnotation.created_at.asc())
            .all()
        )

    @staticmethod
    def soft_delete(db: Session, mapping_id: int, annotation_id: int, actor: str, role: str) -> None:
        annotation = (
            db.query(MappingAnnotation)
            .filter(
                MappingAnnotation.id == annotation_id,
                MappingAnnotation.mapping_id == mapping_id,
                MappingAnnotation.deleted_at.is_(None),
            )
            .first()
        )
        if annotation is None:
            raise HTTPException(status_code=404, detail="annotation not found")
        if annotation.author != actor and role != "admin":
            raise HTTPException(status_code=403, detail="only the author or an admin can delete this comment")

        from sqlalchemy.sql import func as sa_func
        annotation.deleted_at = sa_func.now()
        record_audit(
            db, event_type="mapping.annotation_deleted", actor=actor,
            payload={"mapping_id": mapping_id, "annotation_id": annotation_id},
        )
        db.commit()
        logger.info("[mapping_annotation] stage=deleted mapping_id=%s annotation_id=%s actor=%s",
                    mapping_id, annotation_id, actor)
