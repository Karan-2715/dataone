from sqlalchemy import (
    Column, Float, Integer, String, Text, Boolean, DateTime, JSON, ForeignKey,
    Index,
)
from sqlalchemy.sql import func
from app.core.database import Base


class AutopilotRun(Base):
    __tablename__ = "autopilot_runs"

    id = Column(String, primary_key=True)  # UUID string
    source_id = Column(Integer, nullable=False)
    target_id = Column(Integer, nullable=False)
    mode = Column(String, nullable=False, default="suggest")  # suggest | execute
    model = Column(String, nullable=False, default="llama3")
    status = Column(String, nullable=False, default="running")  # running | completed | failed
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    result_summary = Column(JSON, nullable=True)


class AutopilotLog(Base):
    __tablename__ = "autopilot_logs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String, ForeignKey("autopilot_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    level = Column(String, nullable=False, default="info")  # info | warning | error
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ── Governance layer (DP-AUTO-001, ai_autopilot_tasks #2/#3) ──────────────


class AutopilotPolicy(Base):
    """Per-action-type autonomy policy (FR1). One row per action type;
    absent rows mean the fail-safe default (suggest) applies."""

    __tablename__ = "autopilot_policies"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String, nullable=False, unique=True)
    # disabled | suggest | approve | auto — "auto" only valid for action
    # types the registry marks auto-capable (reversible + low risk).
    autonomy = Column(String, nullable=False, default="suggest")
    max_auto_per_hour = Column(Integer, nullable=False, default=10)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


class AutopilotRecommendation(Base):
    """A proposed action with rationale + confidence (FR2). Approval state
    is folded into ``status`` (INDEX design decision 1) — no separate
    ApprovalRequest table."""

    __tablename__ = "autopilot_recommendations"
    __table_args__ = (
        Index("ix_autopilot_rec_status", "status"),
        Index("ix_autopilot_rec_dedupe", "dedupe_key", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    # Human-readable subject key, e.g. "connection:3" / "mapping:2".
    subject = Column(String, nullable=False)
    dedupe_key = Column(String, nullable=False)  # f"{action_type}:{subject}"
    # {"summary": str, "evidence": [str], "trigger": {...}} — deterministic,
    # metadata-grounded (INDEX design decision 2).
    rationale = Column(JSON, nullable=False, default=dict)
    confidence = Column(Float, nullable=False, default=0.0)  # 0-100
    risk = Column(String, nullable=False)  # low | medium | high
    reversible = Column(Boolean, nullable=False, default=False)
    reversibility_note = Column(Text, nullable=True)
    # pending | approved | rejected | superseded | executing | executed | failed
    status = Column(String, nullable=False, default="pending")
    created_by = Column(String, nullable=False, default="autopilot-engine")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    decided_by = Column(String, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decision_mode = Column(String, nullable=True)  # human | auto
    modified_by = Column(String, nullable=True)
    modified_at = Column(DateTime(timezone=True), nullable=True)
    execution_result = Column(JSON, nullable=True)


class AutopilotActionLog(Base):
    """One row per execution attempt or guardrail block (FR6/FR8)."""

    __tablename__ = "autopilot_action_logs"
    __table_args__ = (
        Index("ix_autopilot_action_mode_started", "mode", "started_at"),
        Index("ix_autopilot_action_type_mode", "action_type", "mode", "started_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    recommendation_id = Column(
        Integer,
        ForeignKey("autopilot_recommendations.id", ondelete="SET NULL"),
        nullable=True,
    )
    action_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    mode = Column(String, nullable=False)  # auto | approved
    # success | failure | blocked_prohibited | blocked_rate_limit
    # | blocked_breaker | blocked_policy
    outcome = Column(String, nullable=False)
    detail = Column(JSON, nullable=True)
    reversibility_note = Column(Text, nullable=True)
    actor = Column(String, nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
