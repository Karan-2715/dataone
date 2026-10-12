"""Async profiling + classification jobs (schema_intel_tasks #2/#3).

Fan-out: profile_connection_task -> one profile_table_task per table ->
one profile_column_task per column. Each column task profiles the column
(bounded sample, per Task #8 Decision 2) and immediately classifies it
using the in-memory sample values (Task #3's value-pattern half) before
persisting only the aggregate ColumnProfile + ColumnClassification rows —
sample_values themselves are never persisted (Task #8 Decision 1).
"""
import logging

from celery import group

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)

# Track which connections have been warned about shared scan credentials
# (Task #8 Decision 4) — process-local, best-effort, not persisted.
_warned_connections: set = set()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def profile_column_task(self, connection_id: int, table_name: str,
                        column_id: int, column_name: str):
    """Profile one column and classify it from the in-memory sample values.
    One task per column for granular retry/parallelism."""
    from app.core.database import SessionLocal
    from app.models.connection import DBConnection
    from app.models.schema_catalog import ColumnClassification, ColumnProfile
    from app.services.audit_helper import record_audit
    from app.services.schema_service import get_connector
    from app.services.security_service import SecurityService
    from app.core.config import settings

    logger.info("[pipeline] stage=profile_column connection_id=%d table=%s column=%s",
                connection_id, table_name, column_name)

    db = SessionLocal()
    try:
        conn = db.query(DBConnection).filter(DBConnection.id == connection_id).first()
        if not conn:
            return {"status": "skipped", "reason": "connection not found"}

        if connection_id not in _warned_connections:
            logger.warning(
                "Profiling connection %d using the same credentials as the Connector "
                "module. Separate read-only scan credentials are recommended for "
                "production (SCHEMA_INTEL_USE_SEPARATE_CREDENTIALS).", connection_id,
            )
            _warned_connections.add(connection_id)

        from app.services.profiling_enrichment import (
            compute_uniqueness_ratio, count_duplicate_values, infer_fk_candidates,
        )

        connector = get_connector(conn)
        try:
            result = connector.profile_column(
                table=table_name, column=column_name,
                sample_limit=settings.SCHEMA_INTEL_SAMPLE_LIMIT,
                distinct_scan_limit=settings.SCHEMA_INTEL_MAX_DISTINCT_SCAN_ROWS,
            )
            # Enrichment (agentic_dba_tasks #2): consumes the in-memory
            # sample while the connector is still open; persists aggregates
            # + candidate metadata only, never sampled values.
            uniqueness_ratio = compute_uniqueness_ratio(
                result.distinct_count, result.row_count,
                scanned_rows=settings.SCHEMA_INTEL_MAX_DISTINCT_SCAN_ROWS)
            duplicate_count = count_duplicate_values(result.sample_values)
            fk_candidates = infer_fk_candidates(
                db, connection_id, connector, table_name, column_name,
                result.sample_values,
                db_type=conn.type,
                max_tables=settings.SCHEMA_INTEL_FK_MAX_TABLES,
                pk_value_limit=settings.SCHEMA_INTEL_FK_PK_VALUE_LIMIT,
                min_overlap=settings.SCHEMA_INTEL_FK_MIN_OVERLAP,
            )
        finally:
            connector.close()

        # Persist aggregates only — sample_values never touches the DB.
        existing = db.query(ColumnProfile).filter(ColumnProfile.column_id == column_id).first()
        if existing:
            existing.null_count = result.null_count
            existing.null_rate = result.null_rate
            existing.distinct_count = result.distinct_count
            existing.min_value = result.min_value
            existing.max_value = result.max_value
            existing.sample_size_used = result.sample_size_used
            existing.row_count = result.row_count
            existing.uniqueness_ratio = uniqueness_ratio
            existing.duplicate_count = duplicate_count
            existing.fk_candidates = fk_candidates
        else:
            db.add(ColumnProfile(
                column_id=column_id,
                null_count=result.null_count,
                null_rate=result.null_rate,
                distinct_count=result.distinct_count,
                min_value=result.min_value,
                max_value=result.max_value,
                sample_size_used=result.sample_size_used,
                row_count=result.row_count,
                uniqueness_ratio=uniqueness_ratio,
                duplicate_count=duplicate_count,
                fk_candidates=fk_candidates,
            ))

        # Classify using the in-memory sample values (value-pattern half of
        # Task #3), unless a human has already overridden this column's
        # classification — an override is a decision, not a cache to
        # invalidate on the next scan.
        existing_classification = (
            db.query(ColumnClassification)
            .filter(ColumnClassification.column_id == column_id)
            .first()
        )
        if existing_classification is None or existing_classification.method != "manual_override":
            classification = SecurityService.classify_column(
                column_name, sample_values=result.sample_values,
            )
            if existing_classification:
                existing_classification.label = classification["label"]
                existing_classification.level = classification["level"]
                existing_classification.confidence = classification["confidence"]
                existing_classification.method = classification["method"]
            else:
                db.add(ColumnClassification(
                    column_id=column_id,
                    label=classification["label"],
                    level=classification["level"],
                    confidence=classification["confidence"],
                    method=classification["method"],
                ))

        db.commit()
        return {
            "status": "completed",
            "column_id": column_id,
            "null_rate": result.null_rate,
            "distinct_count": result.distinct_count,
            "sample_size": result.sample_size_used,
        }
    except Exception as e:
        db.rollback()
        logger.warning("[pipeline] stage=profile_column failed column_id=%d error=%s",
                       column_id, e)
        raise self.retry(exc=e)
    finally:
        db.close()


@celery_app.task
def profile_table_task(connection_id: int, table_name: str, columns: list):
    """Fan out one profile_column_task per column in a table (parallel via
    Celery group())."""
    logger.info("[pipeline] stage=profile_table connection_id=%d table=%s columns=%d",
               connection_id, table_name, len(columns))
    task_group = group(
        profile_column_task.s(
            connection_id=connection_id, table_name=table_name,
            column_id=col["id"], column_name=col["column_name"],
        )
        for col in columns
    )
    task_group.delay()
    return {"table": table_name, "columns_dispatched": len(columns)}


@celery_app.task
def profile_connection_task(connection_id: int):
    """Fan out one profile_table_task per table discovered for a connection.
    Called by POST /api/v1/catalog/{connection_id}/profile."""
    from app.core.database import SessionLocal
    from app.models.schema_catalog import CatalogTable

    logger.info("[pipeline] stage=profile_connection connection_id=%d", connection_id)
    db = SessionLocal()
    try:
        tables = (
            db.query(CatalogTable)
            .filter(CatalogTable.connection_id == connection_id)
            .all()
        )
        if not tables:
            return {"status": "skipped", "reason": "no tables discovered for this connection"}

        for t in tables:
            profile_table_task.delay(
                connection_id=connection_id,
                table_name=t.table_name,
                columns=[{"id": c.id, "column_name": c.column_name} for c in t.columns],
            )

        total_columns = sum(len(t.columns) for t in tables)
        logger.info("[pipeline] stage=profile_connection dispatched tables=%d columns=%d",
                   len(tables), total_columns)
        return {"status": "completed", "connection_id": connection_id,
                "tables": len(tables), "columns": total_columns}
    finally:
        db.close()
