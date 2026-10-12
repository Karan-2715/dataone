"""SchemaDesignPlan model (agentic_dba_tasks #3).

The core reviewable artifact of the Agentic DBA Copilot: a persisted,
stateful plan (survives across chat turns, independently approvable outside
the chat flow) proposing target tables, DQ rules, transformations, and
dialect-aware DDL — always human-approved before anything executes.

Status lifecycle:
    generating -> ready -> applying -> applied
                        \\-> rejected      \\-> partially_applied (mid-plan failure)
              \\-> failed (generation error)

`apply_results` mirrors PipelineRunStep's per-step tracking model, applied
to schema objects: one entry per proposed table with its own
pending/applied/failed/skipped status (agentic_dba_tasks #9).
"""
from __future__ import annotations

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, JSON, String, Text,
)
from sqlalchemy.sql import func

from app.core.database import Base


class SchemaDesignPlan(Base):
    __tablename__ = "schema_design_plans"

    id = Column(Integer, primary_key=True, index=True)
    # Ties into AskData's existing conversation context (chat_sessions).
    session_id = Column(String, nullable=True, index=True)
    question = Column(Text, nullable=False)

    source_connection_id = Column(Integer, ForeignKey("connections.id"),
                                  nullable=False, index=True)
    # Where the proposed tables get created. Nullable = same connection as
    # the source. Schema Mapper requires distinct source/target connections,
    # so draft-mapping auto-creation (task #8) only happens when this is set
    # and differs from source — an honest modeling constraint, not a bug.
    target_connection_id = Column(Integer, ForeignKey("connections.id"),
                                  nullable=True)

    status = Column(String, nullable=False, default="generating", index=True)
    # generating | ready | failed | rejected | applying | applied | partially_applied

    domain_template = Column(String, nullable=True)   # e.g. "retail_analytics", None = catalog-driven
    dialect = Column(String, nullable=True)           # connector type at generation time

    proposed_tables = Column(JSON, nullable=True)     # [{name, columns: [{name, type, nullable, primary_key, source_refs}]}]
    dq_rules = Column(JSON, nullable=True)            # task #4: [{rule, target_table, target_column, justification, confidence, ...}]
    transformations = Column(JSON, nullable=True)     # task #5: [{target_table, target_column, sources, transformation|null, note}]
    generated_ddl = Column(JSON, nullable=True)       # [{table, mode: create|migrate, statements: [sql]}]
    confidence_notes = Column(JSON, nullable=True)    # [str] — plain-language caveats
    apply_results = Column(JSON, nullable=True)       # task #9: [{table, status, error, statements_executed}]
    created_mapping_id = Column(Integer, nullable=True)  # task #8: draft mapping, if auto-created
    error = Column(Text, nullable=True)

    created_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)
    decided_by = Column(String, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
