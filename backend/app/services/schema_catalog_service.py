"""Schema Intel catalog service: persisted discovery (Task #1, FR1/AC1).

Persists what `SchemaService.get_full_schema()` already discovers live, so
search (Task #4), profiling (Task #2), and classification (Task #3) have a
normalized store to read/attach to instead of recomputing from the connector
on every request.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models.connection import DBConnection
from app.models.schema_catalog import (
    CatalogColumn, CatalogForeignKey, CatalogTable, ColumnClassification,
)
from app.services.audit_helper import record_audit
from app.services.schema_service import SchemaService

logger = logging.getLogger(__name__)


class SchemaCatalogService:

    @staticmethod
    def store_table_metadata(
        db: Session, 
        connection_id: int, 
        table_name: str, 
        columns: List[dict],
        source_type: str = "auto_discovery"
    ) -> bool:
        """
        Store table metadata from auto-discovery (e.g. Unity Catalog).
        
        Args:
            db: Database session
            connection_id: Connection ID
            table_name: Full table name (e.g. catalog.schema.table)
            columns: List of column metadata dicts
            source_type: Source of the metadata (e.g. "unity_catalog")
            
        Returns:
            True if successful, False otherwise
        """
        try:
            from app.models.schema_catalog import CatalogTable, CatalogColumn
            from datetime import datetime, timezone
            
            # Check if table already exists
            existing_table = db.query(CatalogTable).filter(
                CatalogTable.connection_id == connection_id,
                CatalogTable.table_name == table_name
            ).first()
            
            if existing_table:
                logger.debug(f"Table {table_name} already exists in catalog")
                return True
                
            # Create new table record
            table = CatalogTable(
                connection_id=connection_id,
                table_name=table_name,
                last_scanned_at=datetime.now(timezone.utc)
            )
            db.add(table)
            db.flush()  # Get the table ID
            
            # Add columns
            for col_info in columns:
                column = CatalogColumn(
                    table_id=table.id,
                    column_name=col_info["name"],
                    data_type=col_info["type"],
                    nullable=col_info.get("nullable", True),
                    is_primary_key=col_info.get("is_primary_key", False),
                    ordinal_position=col_info.get("ordinal_position", 0),
                )
                db.add(column)
            
            db.commit()
            logger.info(f"Stored metadata for table {table_name} ({len(columns)} columns)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store metadata for table {table_name}: {e}")
            db.rollback()
            return False

    @staticmethod
    def search_all(db: Session, q: str, *, page: int, page_size: int) -> dict:
        """Search active connections and their persisted catalog metadata.

        The catalog is local metadata, so global search never opens live
        database connections. Results are ranked exact → prefix → substring,
        then deterministically by connection/table/column name.
        """
        needle = q.strip().lower()
        escaped_needle = needle.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped_needle}%"

        connections = (
            db.query(DBConnection)
            .filter(
                DBConnection.is_deleted.is_(False),
                DBConnection.name.ilike(pattern, escape="\\"),
            )
            .all()
        )
        tables = (
            db.query(CatalogTable, DBConnection)
            .join(DBConnection, DBConnection.id == CatalogTable.connection_id)
            .filter(
                DBConnection.is_deleted.is_(False),
                CatalogTable.table_name.ilike(pattern, escape="\\"),
            )
            .all()
        )
        columns = (
            db.query(CatalogColumn, CatalogTable, DBConnection)
            .join(CatalogTable, CatalogTable.id == CatalogColumn.table_id)
            .join(DBConnection, DBConnection.id == CatalogTable.connection_id)
            .filter(
                DBConnection.is_deleted.is_(False),
                CatalogColumn.column_name.ilike(pattern, escape="\\"),
            )
            .all()
        )

        results = [
            {
                "kind": "connection", "connection_id": conn.id,
                "connection_name": conn.name, "table_id": None,
                "table_name": None, "column_id": None, "column_name": None,
                "data_type": None,
            }
            for conn in connections
        ]
        results.extend({
            "kind": "table", "connection_id": conn.id,
            "connection_name": conn.name, "table_id": table.id,
            "table_name": table.table_name, "column_id": None,
            "column_name": None, "data_type": None,
        } for table, conn in tables)
        results.extend({
            "kind": "column", "connection_id": conn.id,
            "connection_name": conn.name, "table_id": table.id,
            "table_name": table.table_name, "column_id": column.id,
            "column_name": column.column_name, "data_type": column.data_type,
        } for column, table, conn in columns)

        def rank(item: dict) -> tuple:
            value = (
                item["connection_name"] if item["kind"] == "connection"
                else item["table_name"] if item["kind"] == "table"
                else item["column_name"]
            ).lower()
            match_rank = 0 if value == needle else 1 if value.startswith(needle) else 2
            return (
                match_rank, value, item["connection_name"].lower(),
                (item["table_name"] or "").lower(), (item["column_name"] or "").lower(),
            )

        results.sort(key=rank)
        total = len(results)
        start = (page - 1) * page_size
        return {
            "query": q.strip(), "page": page, "page_size": page_size,
            "total": total, "results": results[start:start + page_size],
        }

    @staticmethod
    def _get_connection_or_404(db: Session, connection_id: int) -> DBConnection:
        conn = db.query(DBConnection).filter(DBConnection.id == connection_id).first()
        if not conn:
            raise HTTPException(status_code=404, detail="Connection not found")
        return conn

    @staticmethod
    def scan_connection(db: Session, connection_id: int, *, actor: str) -> dict:
        """Discover the connection's current schema and persist it as a catalog.

        Full-replace semantics per table: a table's columns/foreign-keys are
        deleted and recreated fresh from the connector's current output
        rather than diffed in place (simplest correct approach — this is a
        metadata scan, not a hot path). Tables no longer returned by the
        connector are deleted (cascades to their columns/FKs via the
        relationship's cascade="all, delete-orphan" + FK ondelete="CASCADE").
        """
        conn = SchemaCatalogService._get_connection_or_404(db, connection_id)
        schema = SchemaService.get_full_schema(conn)

        existing_tables = {
            t.table_name: t
            for t in db.query(CatalogTable)
            .filter(CatalogTable.connection_id == connection_id)
            .all()
        }
        seen_table_names = set(schema.keys())
        now = datetime.now(timezone.utc)
        columns_scanned = 0

        for table_name, columns in schema.items():
            table = existing_tables.get(table_name)
            if table is None:
                table = CatalogTable(connection_id=connection_id, table_name=table_name)
                db.add(table)
                db.flush()
            else:
                # Full-replace: drop existing columns (cascades to their FKs)
                # before re-inserting the current set.
                for col in list(table.columns):
                    db.delete(col)
                db.flush()
            table.last_scanned_at = now

            for position, col in enumerate(columns):
                catalog_col = CatalogColumn(
                    table_id=table.id,
                    column_name=col["name"],
                    data_type=col.get("type"),
                    nullable=bool(col.get("nullable", True)),
                    is_primary_key=bool(col.get("primary_key", False)),
                    ordinal_position=position,
                )
                db.add(catalog_col)
                db.flush()
                columns_scanned += 1
                for fk in col.get("foreign_keys") or []:
                    db.add(CatalogForeignKey(
                        column_id=catalog_col.id,
                        references_table=fk["references_table"],
                        references_column=fk["references_column"],
                    ))

        # Remove tables no longer present at the source.
        for table_name, table in existing_tables.items():
            if table_name not in seen_table_names:
                db.delete(table)

        record_audit(
            db, "schema_scanned", actor=actor,
            connection_id=conn.id, connection_name=conn.name,
            payload={"tables_scanned": len(seen_table_names), "columns_scanned": columns_scanned},
        )
        db.commit()

        return {
            "connection_id": connection_id,
            "tables_scanned": len(seen_table_names),
            "columns_scanned": columns_scanned,
            "scanned_at": now,
        }

    @staticmethod
    def get_catalog(
        db: Session, connection_id: int, *,
        q: Optional[str] = None,
        data_type: Optional[str] = None,
        classification_label: Optional[str] = None,
    ) -> List[CatalogTable]:
        """Return the connection's catalog, optionally filtered (Task #4,
        FR4). Filtering is table-scoped: a table is included if it or any
        of its columns matches every supplied filter; matched tables are
        returned with their full column list (not a partial subset) so the
        UI doesn't need to reconcile a collapsed/expanded view."""
        SchemaCatalogService._get_connection_or_404(db, connection_id)
        tables = (
            db.query(CatalogTable)
            .filter(CatalogTable.connection_id == connection_id)
            .options(
                joinedload(CatalogTable.columns).joinedload(CatalogColumn.foreign_keys_rel),
                joinedload(CatalogTable.columns).joinedload(CatalogColumn.profile),
                joinedload(CatalogTable.columns).joinedload(CatalogColumn.classification),
            )
            .order_by(CatalogTable.table_name)
            .all()
        )

        if not (q or data_type or classification_label):
            return tables

        q_lower = q.lower() if q else None
        filtered: List[CatalogTable] = []
        for table in tables:
            table_name_matches = q_lower is not None and q_lower in table.table_name.lower()
            for col in table.columns:
                col_matches_q = q_lower is None or q_lower in col.column_name.lower() or table_name_matches
                col_matches_type = data_type is None or (col.data_type or "").lower() == data_type.lower()
                col_matches_label = (
                    classification_label is None
                    or (col.classification is not None and col.classification.label == classification_label)
                )
                if col_matches_q and col_matches_type and col_matches_label:
                    filtered.append(table)
                    break
        return filtered

    # ── Task #7: manual classification override ────────────────────────

    @staticmethod
    def override_classification(
        db: Session, column_id: int, *, label: str, level: str, actor: str,
    ) -> ColumnClassification:
        column = db.query(CatalogColumn).filter(CatalogColumn.id == column_id).first()
        if not column:
            raise HTTPException(status_code=404, detail="column not found")

        existing = (
            db.query(ColumnClassification)
            .filter(ColumnClassification.column_id == column_id)
            .first()
        )
        before = {
            "label": existing.label, "level": existing.level, "method": existing.method,
        } if existing else None
        now = datetime.now(timezone.utc)

        if existing:
            existing.label = label
            existing.level = level
            existing.confidence = 1.0  # A human decision is treated as fully confident
            existing.method = "manual_override"
            existing.overridden_by = actor
            existing.overridden_at = now
            row = existing
        else:
            row = ColumnClassification(
                column_id=column_id, label=label, level=level,
                confidence=1.0, method="manual_override",
                overridden_by=actor, overridden_at=now,
            )
            db.add(row)

        db.flush()
        record_audit(
            db, "classification_overridden", actor=actor,
            payload={
                "column_id": column_id, "before": before,
                "after": {"label": label, "level": level, "method": "manual_override"},
            },
        )
        db.commit()
        db.refresh(row)
        return row
