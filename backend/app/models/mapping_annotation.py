"""Async collaboration annotations/comments on mappings (Enterprise v2, E16).

Scoped to what's achievable without realtime infrastructure (E16-1/2/3):
a threaded comment attached to a mapping, optionally pinned to one field
mapping (target table.column) edge. Steward comments additionally
snapshot the review_stage they were made at, so "rejected because..."
context survives later stage transitions — see
mapping_review_service.MappingReviewService.transition, the only writer
of kind="steward_comment" rows (never user-supplied, to keep the label
honest). Presence/live co-editing (E16-4/5/6) need a transport +
security decision and are explicitly out of scope here.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base

ANNOTATION_KINDS = ("comment", "steward_comment")


class MappingAnnotation(Base):
    __tablename__ = "mapping_annotations"

    id = Column(Integer, primary_key=True, index=True)
    mapping_id = Column(Integer, ForeignKey("mappings.id", ondelete="CASCADE"), nullable=False, index=True)
    # NULL edge_id = a mapping-level comment; set = pinned to one table.column edge.
    edge_id = Column(Integer, ForeignKey("field_mappings.id", ondelete="CASCADE"), nullable=True, index=True)
    # NULL parent_id = a top-level comment; set = a threaded reply.
    parent_id = Column(Integer, ForeignKey("mapping_annotations.id", ondelete="CASCADE"), nullable=True, index=True)

    kind = Column(String, nullable=False, default="comment")  # one of ANNOTATION_KINDS
    review_stage = Column(String, nullable=True)  # snapshot at authoring time; steward_comment only

    author = Column(String, nullable=False)
    body = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
