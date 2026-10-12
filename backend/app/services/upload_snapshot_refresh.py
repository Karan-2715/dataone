"""Keep cached Unity Catalog schema snapshots in sync with files uploaded via Quick Upload.

``GET /connectors/{id}/schema`` serves a cached ``SchemaSnapshot`` and never rescans, so without this a
freshly uploaded table stays invisible (the schema shows ``(empty_schema)``) until someone re-runs discovery.
"""
import copy
import hashlib
import json
import logging
from typing import List

from sqlalchemy.orm import Session

from app.models.connection import DBConnection
from app.models.schema_snapshot import SchemaSnapshot

logger = logging.getLogger(__name__)


def refresh_snapshots_for_uploaded_table(
    db: Session, catalog: str, schema_name: str, table_name: str, columns: List[str],
) -> int:
    """Add ``catalog.schema.table`` to every Databricks connection's snapshot. Returns snapshots updated."""
    full_name = f"{catalog}.{schema_name}.{table_name}"
    placeholder = f"{catalog}.{schema_name}.(empty_schema)"
    column_meta = [
        {"name": c, "type": "string", "nullable": True, "primary_key": False,
         "foreign_keys": [], "ordinal_position": i + 1, "default": None}
        for i, c in enumerate(columns)
    ]

    connections = (
        db.query(DBConnection)
        .filter(DBConnection.type.ilike("databricks"), DBConnection.is_deleted == False)  # noqa: E712
        .all()
    )
    updated = 0
    for conn in connections:
        snapshot = db.query(SchemaSnapshot).filter(SchemaSnapshot.connection_id == conn.id).first()
        if snapshot is None:
            continue  # nothing cached; the first /schema call does a live scan that will see the table
        data = copy.deepcopy(snapshot.schema_json or {})
        data.pop(placeholder, None)
        data[full_name] = column_meta
        snapshot.schema_json = data
        snapshot.schema_hash = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
        updated += 1
    logger.info("[databricks_ingest] stage=snapshot_refresh table=%s snapshots_updated=%d", full_name, updated)
    return updated
