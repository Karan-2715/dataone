"""Schema Intel catalog models (Task #1, FR1/AC1).

Tables:
    catalog_tables        — one row per discovered table per connection.
    catalog_columns       — one row per column, child of catalog_tables.
    catalog_foreign_keys  — one row per detected FK reference, child of catalog_columns.

Persists what `SchemaService.get_full_schema()` already discovers live on every
request, so search (Task #4), profiling (Task #2), and classification (Task #3)
have a normalized store to attach to instead of recomputing from the connector
on every call. See `SchemaCatalogService.scan_connection` for the
full-replace-per-table upsert that populates these tables.
"""
from __future__ import annotations

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float, ForeignKey, JSON, UniqueConstraint,
)
from sqlalchemy.orm import backref, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class CatalogTable(Base):
    __tablename__ = "catalog_tables"
    __table_args__ = (
        UniqueConstraint("connection_id", "table_name", name="uq_catalog_table_per_connection"),
    )

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    table_name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)
    last_scanned_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    columns = relationship(
        "CatalogColumn",
        back_populates="table",
        cascade="all, delete-orphan",
    )


class CatalogColumn(Base):
    __tablename__ = "catalog_columns"
    __table_args__ = (
        UniqueConstraint("table_id", "column_name", name="uq_catalog_column_per_table"),
    )

    id = Column(Integer, primary_key=True, index=True)
    table_id = Column(Integer, ForeignKey("catalog_tables.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    column_name = Column(String, nullable=False)
    data_type = Column(String, nullable=True)
    nullable = Column(Boolean, nullable=False, default=True)
    is_primary_key = Column(Boolean, nullable=False, default=False)
    ordinal_position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    table = relationship("CatalogTable", back_populates="columns")
    # Named to avoid clashing with SQLAlchemy's own `foreign_keys` kwarg.
    foreign_keys_rel = relationship(
        "CatalogForeignKey",
        back_populates="column",
        cascade="all, delete-orphan",
    )


class CatalogForeignKey(Base):
    __tablename__ = "catalog_foreign_keys"

    id = Column(Integer, primary_key=True, index=True)
    column_id = Column(Integer, ForeignKey("catalog_columns.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    references_table = Column(String, nullable=False)
    references_column = Column(String, nullable=False)

    column = relationship("CatalogColumn", back_populates="foreign_keys_rel")


class ColumnProfile(Base):
    """Per-column profiling metrics (Task #2, FR2/FR7).

    No raw sample values are persisted here — schema_intel_tasks Task #8
    Decision 1 requires sample data to stay in-memory for the duration of
    a single profiling task and never touch a database column, log line,
    audit payload, or API response.
    """
    __tablename__ = "column_profiles"
    __table_args__ = (
        UniqueConstraint("column_id", name="uq_column_profile"),
    )

    id = Column(Integer, primary_key=True, index=True)
    column_id = Column(Integer, ForeignKey("catalog_columns.id", ondelete="CASCADE"),
                       nullable=False, unique=True, index=True)

    null_count = Column(Integer, nullable=False, default=0)
    null_rate = Column(Float, nullable=False, default=0.0)        # 0.0 - 1.0
    distinct_count = Column(Integer, nullable=True)               # None if too expensive/unsupported
    min_value = Column(String, nullable=True)                     # String-ified, connector-agnostic
    max_value = Column(String, nullable=True)
    sample_size_used = Column(Integer, nullable=False, default=0) # Metadata only, not the data itself

    # ── Profiling enrichment (agentic_dba_tasks #2) — all additive/nullable.
    # Running dev Postgres needs a manual ALTER (recorded in the epic's
    # INDEX.md progress log, per repo convention for catalog model changes).
    row_count = Column(Integer, nullable=True)                    # total table rows at profile time
    uniqueness_ratio = Column(Float, nullable=True)               # distinct_count / row_count, 0.0-1.0
    duplicate_count = Column(Integer, nullable=True)              # distinct sampled values seen >1x —
                                                                  # an aggregate; the values themselves
                                                                  # stay in-memory (Task #8 Decision 1)
    fk_candidates = Column(JSON, nullable=True)                   # [{table, column, overlap_ratio}] —
                                                                  # heuristic hints w/ confidence, never
                                                                  # asserted facts

    profiled_at = Column(DateTime(timezone=True), server_default=func.now(),
                         onupdate=func.now(), nullable=False)

    column = relationship("CatalogColumn", backref=backref("profile", uselist=False))


class ColumnClassification(Base):
    """Persisted PII/sensitivity classification for a column (Task #3, FR3/FR5).

    Replaces the previous recompute-on-every-request model
    (SecurityService.classify_column) so there's a real row for Task #7's
    manual override to attach to, and for Task #4's search/filter to query.
    """
    __tablename__ = "column_classifications"
    __table_args__ = (
        UniqueConstraint("column_id", name="uq_column_classification"),
    )

    id = Column(Integer, primary_key=True, index=True)
    column_id = Column(Integer, ForeignKey("catalog_columns.id", ondelete="CASCADE"),
                       nullable=False, unique=True, index=True)

    label = Column(String, nullable=False)       # "PII" | "Sensitive" | "Public"
    level = Column(String, nullable=False)        # "High" | "Medium" | "Low"
    confidence = Column(Float, nullable=False, default=0.0)  # 0.0 - 1.0
    # "keyword" (name-based) | "value_pattern" (content-based, Task #3 AC2) |
    # "manual_override" (Task #7)
    method = Column(String, nullable=False, default="keyword")
    overridden_by = Column(String, nullable=True)  # actor email, if method == manual_override
    overridden_at = Column(DateTime(timezone=True), nullable=True)

    classified_at = Column(DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    column = relationship("CatalogColumn", backref=backref("classification", uselist=False))
