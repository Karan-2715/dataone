"""Per-event-type notify-out opt-in flags (aci_integration_tasks #5/#7).

One row per event key, disabled by default (INDEX design decision #9: no
blanket "notify everything" — alert fatigue defeats the point). Event keys
are namespaced strings, e.g.:

    autopilot:<action_type>            recommendation entered pending-approval
    agentic_dba:schema_design_create   a SchemaDesignPlan became ready
    pipeline:run_failure               a PipelineRun reached failed
    pipeline:run_success               a PipelineRun reached succeeded
    pipeline:drift_impact              pre-run drift check flagged a pipeline

Failures and successes are independently configurable on purpose — most
teams want failure alerts but not a message per successful daily run.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class NotificationSetting(Base):
    __tablename__ = "notification_settings"

    id = Column(Integer, primary_key=True, index=True)
    event_key = Column(String, nullable=False, unique=True, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)
