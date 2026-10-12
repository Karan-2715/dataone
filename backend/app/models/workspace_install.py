"""Record of what DataOne installed into a user's Databricks workspace.

One row per workspace host. Re-running the installer updates the row, so
``release`` always reflects the code currently deployed there and the UI can
tell when a newer DataOne release needs to be pushed.
"""
from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class WorkspaceInstall(Base):
    __tablename__ = "workspace_installs"

    id = Column(Integer, primary_key=True, index=True)
    workspace_host = Column(String(255), nullable=False, unique=True, index=True)
    catalog = Column(String(255), nullable=False)
    schema_prefix = Column(String(128), nullable=False)
    notebook_folder = Column(String(512), nullable=False)
    secret_scope = Column(String(128), nullable=False)
    job_id = Column(BigInteger, nullable=True)
    release = Column(String(32), nullable=False)
    installed_by = Column(String(255), nullable=False)
    steps = Column(JSON, nullable=True)
    installed_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
