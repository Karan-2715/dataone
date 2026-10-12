"""Visualize saved-view model (Visualize Task #1, VIZ-T5).

A VizView pins a chart configuration (data source + dimensions/measures/
filters + chart type) so a user can reload it later without re-building
the query from scratch.
"""
from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, JSON, String, DateTime
from sqlalchemy.sql import func

from app.core.database import Base


class VizView(Base):
    __tablename__ = "viz_views"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    table_name = Column(String, nullable=False)
    chart_type = Column(String, nullable=False, default="table")  # table|bar|line|area|pie|scatter|kpi
    dimensions = Column(JSON, nullable=False, default=list)       # [str, ...]
    measures = Column(JSON, nullable=False, default=list)         # [{field, aggregation}, ...]
    filters = Column(JSON, nullable=False, default=list)          # [{field, operator, value}, ...]
    created_by = Column(String, nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)
