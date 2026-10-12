from sqlalchemy import (
    Column, Integer, String, JSON, DateTime, Boolean, Text, Index, text,
)
from sqlalchemy.sql import func
from datetime import datetime
from app.core.database import Base


class DBConnection(Base):
    """
    Stores connection parameters for source/target databases.

    `config` holds non-secret parameters (host, port, dbname, path, ...).
    Until the secret-manager task (connector_tasks #2) lands, legacy rows
    may still carry secret fields inside `config` — the API layer redacts
    them on every response (see connector_catalog.redact_config); nothing
    outside the service layer may serialize `config` raw.
    """
    __tablename__ = "connections"

    id = Column(Integer, primary_key=True, index=True)
    # Uniqueness is enforced only among non-deleted rows (partial index
    # below) so a soft-deleted name can be reused by a new connection.
    name = Column(String, index=True, nullable=False)
    type = Column(String, nullable=False)  # 'postgres', 'sqlite', 'mysql', etc.
    # Organizational label only; this does NOT provide access/isolation boundaries.
    environment = Column(String, nullable=False, default="dev", server_default="dev")
    config = Column(JSON, nullable=False)  # {host, port, dbname, path, etc.}
    # Reference key into the secret manager. Null until connector_tasks #2
    # migrates secrets out of `config`.
    secrets_ref = Column(String, nullable=True)
    # unknown | healthy | degraded | down — written by test-connection and
    # the health-check scheduler (connector_tasks #4/#5).
    health_status = Column(String, nullable=False, default="unknown")
    last_tested_at = Column(DateTime(timezone=True), nullable=True)
    last_test_error = Column(Text, nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String, nullable=True)
    updated_by = Column(String, nullable=True)
    # Owner for per-user isolation (tenant_isolation_tasks slice #1: connections
    # only). A string email, matching this codebase's existing actor convention
    # (created_by/updated_by/record_audit's actor) rather than an integer FK.
    owner_email = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=True)

    __table_args__ = (
        Index(
            "uq_connection_name_active", "owner_email", "name",
            unique=True,
            postgresql_where=text("NOT is_deleted"),
            sqlite_where=text("NOT is_deleted"),
        ),
    )
