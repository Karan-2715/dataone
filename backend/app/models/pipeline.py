"""Pipeline workspace models (Pipelines upgrade, DP-PIPE-001).

Tables:
    pipelines             — top-level pipeline definition (source, target, pinned mapping).
    schedules             — cron schedule attached to a pipeline (FR4).
    retry_policies        — per-pipeline retry config (FR7).
    pipeline_runs         — one row per pipeline execution (FR6).
    pipeline_run_steps    — per-step E-T-L granularity (FR5, FR6).

Naming follows the established convention in app/models/mapping.py.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, CheckConstraint,
    Column, Integer, String, Text, DateTime, ForeignKey, JSON, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Pipeline(Base):
    __tablename__ = "pipelines"

    id = Column(Integer, primary_key=True, index=True)
    # Nullable until app-wide tenant_id lands (mapper_tasks #7). When
    # added, set nullable=False and add a WHERE filter to every query.
    tenant_id = Column(String, nullable=True, index=True)
    name = Column(String, nullable=False)
    source_connection_id = Column(
        Integer, ForeignKey("connections.id", ondelete="RESTRICT"), nullable=False,
    )
    target_connection_id = Column(
        Integer, ForeignKey("connections.id", ondelete="RESTRICT"), nullable=False,
    )
    # Pinned at create time so drift checks (Task #2) have a stable
    # baseline. The published mapping_version is what gets executed.
    mapping_id = Column(
        Integer, ForeignKey("mappings.id", ondelete="RESTRICT"), nullable=False,
    )
    mapping_version_id = Column(
        Integer, ForeignKey("mapping_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    enabled = Column(Boolean, nullable=False, default=True)
    execution_mode = Column(
        String, nullable=False, default="manual",
    )  # auto | manual — "auto" triggers a run immediately on create
    load_strategy = Column(
        String, nullable=True,
    )  # upsert | full_refresh | append | null=auto-infer from natural keys
    created_by = Column(String, nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    # Relationships
    source_connection = relationship("DBConnection", foreign_keys=[source_connection_id])
    target_connection = relationship("DBConnection", foreign_keys=[target_connection_id])

    # Schedule.pipeline_id is unique — 1:1, matching the singular
    # `schedule` field in PipelineReadWithRelations (Task #4 wires it).
    schedule = relationship(
        "Schedule", back_populates="pipeline", cascade="all, delete-orphan",
        uselist=False,
    )
    retry_policy = relationship(
        "RetryPolicy", back_populates="pipeline", cascade="all, delete-orphan",
        uselist=False,
    )
    runs = relationship(
        "PipelineRun", back_populates="pipeline", cascade="all, delete-orphan",
    )


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, index=True)
    pipeline_id = Column(
        Integer, ForeignKey("pipelines.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    cron_expression = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    timezone = Column(String, nullable=False, default="UTC")
    # Maintained by the scheduler (Task #4). Null until the schedule is
    # first registered with the beat; scheduler populates on every tick.
    next_run_at = Column(DateTime(timezone=True), nullable=True)

    pipeline = relationship("Pipeline", back_populates="schedule")


class RetryPolicy(Base):
    __tablename__ = "retry_policies"

    id = Column(Integer, primary_key=True, index=True)
    pipeline_id = Column(
        Integer, ForeignKey("pipelines.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    max_attempts = Column(Integer, nullable=False, default=3)
    backoff_seconds = Column(Integer, nullable=False, default=60)
    # Optional JSON list of substrings matched against error_message;
    # only matching errors trigger a retry. Empty list means "retry all
    # transient failures" (the executor decides what's transient).
    retryable_error_patterns = Column(JSON, nullable=True)

    __table_args__ = (
        CheckConstraint("max_attempts >= 1", name="ck_retry_max_attempts_positive"),
    )

    pipeline = relationship("Pipeline", back_populates="retry_policy")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, index=True)
    pipeline_id = Column(
        Integer, ForeignKey("pipelines.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    status = Column(
        String, nullable=False, default="pending",
    )  # pending | running | succeeded | failed | retrying
    trigger = Column(
        String, nullable=False, default="manual",
    )  # manual | scheduled | rerun
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    rows_processed = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    # Self-FK: a re-run (FR8) creates a new row whose parent_run_id
    # points at the original.
    parent_run_id = Column(
        Integer, ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    pipeline = relationship("Pipeline", back_populates="runs")
    steps = relationship(
        "PipelineRunStep", back_populates="run", cascade="all, delete-orphan",
    )

    # Index that enforces "at most one active run per pipeline" at the DB
    # level (Task #9). Postgres would use a partial unique index; SQLite
    # doesn't support partial indexes, so the unique constraint is on
    # (pipeline_id, status) for active statuses only via application logic.
    # The app layer guards the create endpoint with a SELECT-then-INSERT
    # race-window check; the DB-level guarantee is added in Task #9.
    __table_args__ = (
        Index("ix_pipeline_runs_pipeline_started", "pipeline_id", "started_at"),
    )


class PipelineRunStep(Base):
    __tablename__ = "pipeline_run_steps"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(
        Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step = Column(String, nullable=False)  # extract | transform | load
    status = Column(String, nullable=False, default="pending")
    # pending | running | succeeded | failed | skipped
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    rows_processed = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)

    run = relationship("PipelineRun", back_populates="steps")
