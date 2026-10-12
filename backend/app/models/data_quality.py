"""Data-quality results pushed from Databricks (PySpark job) into DataOne.

One ``DataQualityRun`` row per job execution (idempotent on ``run_id``) with
one ``DataQualityTableResult`` per profiled table. Scores are 0-100; a
dimension that could not be measured is stored as NULL and surfaced as
"not available" by the UI — never as a fabricated number.
"""
from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class DataQualityRun(Base):
    __tablename__ = "data_quality_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(128), nullable=False, unique=True, index=True)
    source = Column(String(64), nullable=False, default="databricks")
    catalog = Column(String(255), nullable=True)
    schema_name = Column(String(255), nullable=True)
    table_count = Column(Integer, nullable=False, default=0)
    total_rows = Column(BigInteger, nullable=True)
    overall_score = Column(Float, nullable=True)
    completeness = Column(Float, nullable=True)
    uniqueness = Column(Float, nullable=True)
    validity = Column(Float, nullable=True)
    freshness = Column(Float, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tables = relationship(
        "DataQualityTableResult",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="DataQualityTableResult.overall_score",
    )


class DataQualityTableResult(Base):
    __tablename__ = "data_quality_table_results"

    id = Column(Integer, primary_key=True, index=True)
    run_pk = Column(Integer, ForeignKey("data_quality_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    table_name = Column(String(512), nullable=False)
    row_count = Column(BigInteger, nullable=True)
    column_count = Column(Integer, nullable=True)
    completeness = Column(Float, nullable=True)
    uniqueness = Column(Float, nullable=True)
    validity = Column(Float, nullable=True)
    freshness = Column(Float, nullable=True)
    overall_score = Column(Float, nullable=True)
    last_modified = Column(DateTime(timezone=True), nullable=True)
    issues = Column(JSON, nullable=True)  # worst columns / failed rules, capped by the schema

    run = relationship("DataQualityRun", back_populates="tables")
