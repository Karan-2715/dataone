"""Governance Intelligence Center metadata (Enterprise v2, E08).

Replaces the hardcoded `dama_metadata` block in security_service.py
(three canned owner/steward/retention strings keyed only by PII level,
never configurable, never attached to a real asset) with a real,
editable, per-asset record: one row per connection, table, or column.
NULL table_name/column_name means the record governs the whole
connection/table respectively — a coarser row is the fallback when a
finer one doesn't exist (see GovernanceService.get_effective).

This is authoring/read-only metadata, not enforcement. Retention
*enforcement* (E08-7) and compliance *reporting* (E08-8) are explicitly
out of scope here — see E08 spec.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base

# v1 classification taxonomy — the same 3 labels SecurityService already
# uses (PII/Sensitive/Public), plus Confidential for governance-only
# assets that aren't a PII signal. A managed multi-value taxonomy admin
# (E08-3) is deferred; this is a validated fixed set, not free text.
CLASSIFICATION_LABELS = ("PII", "Sensitive", "Confidential", "Public")
COMPLIANCE_STATUSES = ("not_assessed", "under_review", "compliant", "non_compliant")


class GovernanceMetadata(Base):
    __tablename__ = "governance_metadata"
    __table_args__ = (
        UniqueConstraint("connection_id", "table_name", "column_name",
                         name="uq_governance_metadata_asset"),
    )

    id = Column(Integer, primary_key=True, index=True)
    # Nullable until app-wide tenant_id lands (same placeholder precedent
    # as Pipeline.tenant_id — mapper_tasks #7). When added, set
    # nullable=False and add a WHERE filter to every query.
    tenant_id = Column(String, nullable=True, index=True)

    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    table_name = Column(String, nullable=True)   # NULL = governs the whole connection
    column_name = Column(String, nullable=True)  # NULL = governs the whole table

    owner = Column(String, nullable=True)
    steward = Column(String, nullable=True)
    classification = Column(String, nullable=True)       # one of CLASSIFICATION_LABELS
    retention_policy = Column(String, nullable=True)      # free-text policy description
    compliance_status = Column(String, nullable=False, default="not_assessed")

    updated_by = Column(String, nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)
