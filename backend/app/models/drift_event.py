"""DriftEvent — persisted column-level schema drift record (Task #6, FR6/AC3).

The existing `check_schema_drift_task` already computes a full column-level
diff via `DiffService.compare_schemas` but discards it before persistence,
writing only a table-count summary into the AuditLog payload. This model
captures the structured diff so the drift-history endpoint can answer
"what changed" without the caller re-diffing two raw JSON blobs.

Standalone from the Task #1 catalog tables — this task is intentionally
decoupled from the catalog build (see INDEX.md execution order) and works
against the already-shipped `SchemaSnapshot` model alone.
"""
from sqlalchemy import Column, Integer, DateTime, JSON, ForeignKey, Index
from sqlalchemy.sql import func
from app.core.database import Base


class DriftEvent(Base):
    __tablename__ = "drift_events"

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(
        Integer,
        ForeignKey("connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_id = Column(
        Integer,
        ForeignKey("schema_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Nullable for the first-ever scan (no previous snapshot to diff against).
    previous_snapshot_id = Column(
        Integer,
        ForeignKey("schema_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Table-level changes (JSON lists of table names).
    tables_added = Column(JSON, nullable=False, default=list)
    tables_removed = Column(JSON, nullable=False, default=list)
    # Column-level changes (JSON lists of structured objects).
    # columns_added: [{"table": str, "column": str}]
    # columns_removed: [{"table": str, "column": str}]
    # type_changes: [{"table": str, "column": str, "old_type": str, "new_type": str}]
    columns_added = Column(JSON, nullable=False, default=list)
    columns_removed = Column(JSON, nullable=False, default=list)
    type_changes = Column(JSON, nullable=False, default=list)
    detected_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_drift_events_conn_detected", "connection_id", "detected_at"),
    )