"""Mapping Review & Approval Workflow (Enterprise v2, E13).

Adds a governance state machine on top of the mapping workspace's
existing draft/published `status` — deliberately independent (see
Mapping.review_stage's docstring in app/models/mapping.py). Every
transition is role-gated and audited via the existing audit helper;
nothing here mutates `status`, `current_version_id`, or edges.

Role gating is a documented v1 simplification: this RBAC system has
only admin/analyst/viewer roles today, no distinct "business approver"
or "data steward" roles. Early-stage transitions (submit for review)
allow admin or analyst; approval transitions (business/steward/
production) require admin. Tightening this to real dedicated roles is
future RBAC work, not guessed at here.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.audit_helper import record_audit
from app.services.mapping_service import MappingService

logger = logging.getLogger(__name__)

STAGES = (
    "draft", "ai_proposed", "pending_review",
    "business_approved", "steward_approved", "production_ready", "rejected",
)

# to_stage -> (allowed from_stages, roles allowed to make this transition)
_TRANSITIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "ai_proposed": (("draft",), ("admin", "analyst")),
    "pending_review": (("draft", "ai_proposed", "rejected"), ("admin", "analyst")),
    "business_approved": (("pending_review",), ("admin",)),
    "steward_approved": (("business_approved",), ("admin",)),
    "production_ready": (("steward_approved",), ("admin",)),
    "rejected": (("ai_proposed", "pending_review", "business_approved", "steward_approved"), ("admin", "analyst")),
    # Resubmission back to draft: from a rejection, or — build-validation
    # B-E13-08 — from production_ready itself, since without this a
    # mapping needing revision after go-live (e.g. a source column type
    # changed) had no way back into the workflow at all and would need
    # to be recreated from scratch. Intentionally the same allowed_from
    # role tuple as the existing rejected->draft path, not a new gate.
    "draft": (("rejected", "production_ready"), ("admin", "analyst")),
}


class MappingReviewService:
    @staticmethod
    def transition(
        db: Session, mapping_id: int, to_stage: str, actor: str, role: str,
        note: str | None = None,
    ) -> Any:
        if to_stage not in STAGES:
            raise HTTPException(status_code=422, detail=f"Unknown review stage '{to_stage}'; allowed: {STAGES}")

        mapping = MappingService.get_mapping(db, mapping_id)
        allowed_from, allowed_roles = _TRANSITIONS[to_stage]

        if mapping.review_stage not in allowed_from:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot move from '{mapping.review_stage}' to '{to_stage}' — "
                    f"allowed only from {allowed_from}"
                ),
            )
        if role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"role '{role}' cannot make this transition; need one of {allowed_roles}",
            )

        from_stage = mapping.review_stage
        mapping.review_stage = to_stage
        db.commit()
        db.refresh(mapping)

        logger.info("[mapping_review] stage=transitioned mapping_id=%s from=%s to=%s actor=%s",
                    mapping_id, from_stage, to_stage, actor)
        record_audit(
            db, event_type="mapping.review_transitioned", actor=actor,
            payload={"mapping_id": mapping_id, "from_stage": from_stage, "to_stage": to_stage},
        )

        # E16-3 — an optional note on a transition becomes a steward
        # comment tied to that exact stage change ("rejected because...").
        if note:
            from app.services.mapping_annotation_service import MappingAnnotationService
            MappingAnnotationService.add_steward_comment(
                db, mapping_id, author=actor, body=note, review_stage=to_stage,
            )
            db.commit()

        return mapping

    @staticmethod
    def get_queue(db: Session, stage: str | None = None) -> list[Any]:
        """Mappings awaiting action — defaults to every non-terminal,
        non-draft stage (the review queue); a specific stage narrows it.

        Excluding "draft" from the default is deliberate, not an oversight
        (build-validation B-E13-09 raised this as a gap): a draft hasn't
        been submitted for anyone else's review yet, so it isn't
        "awaiting action" in the sense this endpoint means — dumping every
        in-progress draft into the same bucket as items a business/steward
        approver actually needs to act on would dilute the one thing this
        queue is for. Drafts remain fully discoverable two other ways: the
        plain mapping list (GET /mappings, unfiltered by review_stage) and
        this same endpoint with an explicit ?stage=draft.
        """
        from app.models.mapping import Mapping

        query = db.query(Mapping).filter(Mapping.deleted_at.is_(None))
        if stage is not None:
            if stage not in STAGES:
                raise HTTPException(status_code=422, detail=f"Unknown review stage '{stage}'; allowed: {STAGES}")
            query = query.filter(Mapping.review_stage == stage)
        else:
            query = query.filter(Mapping.review_stage.in_(
                ("ai_proposed", "pending_review", "business_approved", "steward_approved"),
            ))
        return query.order_by(Mapping.updated_at.desc()).all()
