"""
IngestionRun and IngestionPipelineCatalog models.

IngestionRun tracks a single Databricks Job run triggered by DataOne.
IngestionPipelineCatalog stores the mapping of source_type -> databricks_job_id
so we can reuse existing jobs idempotently.
"""
from __future__ import annotations
from sqlalchemy import Column, Integer, String, Text, DateTime, BigInteger, JSON
from sqlalchemy.sql import func
from app.core.database import Base


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id = Column(Integer, primary_key=True, index=True)
    databricks_run_id = Column(BigInteger, nullable=True, index=True)  # from Databricks
    databricks_job_id = Column(BigInteger, nullable=True)              # Databricks Job ID
    source_type = Column(String, nullable=False)                       # mysql, postgres, mongodb, etc.
    source_connection_id = Column(Integer, nullable=True)              # DataOne connection ID
    target_connection_id = Column(Integer, nullable=True)              # DataOne connection ID
    status = Column(String, nullable=False, default="pending")         # pending | running | succeeded | failed | cancelled
    rows_ingested = Column(BigInteger, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    actor = Column(String, nullable=True)                              # user email who triggered
    trigger_params = Column(JSON, nullable=True)                       # params passed to Databricks job
    databricks_run_url = Column(Text, nullable=True)                   # link to Databricks run page
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IngestionPipelineCatalog(Base):
    __tablename__ = "ingestion_pipeline_catalog"
    id = Column(Integer, primary_key=True, index=True)
    source_type = Column(String, nullable=False, unique=True, index=True)  # mysql, postgres, mongodb, csv, etc.
    databricks_job_id = Column(BigInteger, nullable=False)                 # Databricks Job ID
    job_name = Column(String, nullable=False)                              # human readable name
    pipeline_type = Column(String, nullable=False, default="job")          # job | dlt
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
