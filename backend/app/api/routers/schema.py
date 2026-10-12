from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.connection import DBConnection
from app.models.mapping import FieldMapping, Mapping
from app.services.schema_service import SchemaService
from app.services.diff_service import DiffService
from app.services.security_service import SecurityService
from app.services.ai_service import AIService
from app.services.audit_helper import record_audit

router = APIRouter()


def _get_connection_or_404(id: int, db: Session) -> DBConnection:
    """Load a DBConnection by id or raise 404."""
    conn = db.query(DBConnection).filter(
        DBConnection.id == id,
        DBConnection.is_deleted == False,  # noqa: E712
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


def _get_real_table_mappings(
    db: Session, source_conn_id: int, target_conn_id: int,
) -> Dict[str, list[Dict[str, Any]]]:
    """Look up the published Schema Mapper mapping (if any) between these two
    connections and group its field-level edges into table-level
    correspondences (source_table -> [{target_table, field_count, has_transformation}]).

    The Schema Topology graph previously matched tables by exact name only,
    which is wrong whenever a real ETL mapping renames a table (e.g.
    crm_users -> dw_customers) — every renamed source table was flagged
    "not found in target schema" despite a real, working, published mapping
    existing between them. This mirrors pipeline_executor.py's own
    "current published version's field mappings" resolution so the topology
    graph and the actual pipeline execution agree on what's mapped.
    """
    mapping = (
        db.query(Mapping)
        .filter(
            Mapping.source_id == source_conn_id,
            Mapping.target_id == target_conn_id,
            Mapping.status == "published",
            Mapping.deleted_at.is_(None),
        )
        .order_by(Mapping.updated_at.desc())
        .first()
    )
    if mapping is None or mapping.current_version_id is None:
        return {}

    edges = (
        db.query(FieldMapping)
        .filter(FieldMapping.version_id == mapping.current_version_id)
        .all()
    )

    table_pairs: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for edge in edges:
        sources = edge.sources or []
        if not sources or not sources[0].get("table"):
            continue
        source_table = sources[0]["table"]
        by_target = table_pairs.setdefault(source_table, {})
        entry = by_target.setdefault(edge.target_table, {
            "target_table": edge.target_table,
            "field_count": 0,
            "has_transformation": False,
        })
        entry["field_count"] += 1
        if (edge.transformation or {}).get("kind", "direct") != "direct":
            entry["has_transformation"] = True
    return {
        source_table: list(by_target.values())
        for source_table, by_target in table_pairs.items()
    }

@router.get("/diff")
def compare_schemas(source_id: int, target_id: int, db: Session = Depends(get_db)):
    """
    Compare two connected database schemas and find structural differences.
    """
    source_conn = _get_connection_or_404(source_id, db)
    target_conn = _get_connection_or_404(target_id, db)

    if not source_conn or not target_conn:
        raise HTTPException(status_code=404, detail="Source or Target Connection not found")

    try:
        source_schema = SchemaService.get_full_schema(source_conn)
        target_schema = SchemaService.get_full_schema(target_conn)
        
        diff_results = DiffService.compare_schemas(source_schema, target_schema)
        return {
            "source": source_conn.name,
            "target": target_conn.name,
            "diff": diff_results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Diff failed: {str(e)}")

@router.get("/{id}/classify")
def classify_schema(id: int, db: Session = Depends(get_db)):
    """
    Apply DAMA data governance classifications to columns structure metadata.
    """
    db_conn = db.query(DBConnection).filter(DBConnection.id == id).first()
    if not db_conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    try:
        schema_data = SchemaService.get_full_schema(db_conn)
        classifications = SecurityService.classify_schema(schema_data)
        pii_count = sum(
            1 for cols in classifications.values()
            for c in cols
            if isinstance(c, dict) and c.get("classification", {}).get("level") == "High"
        )
        record_audit(db, "schema_classified", connection_id=db_conn.id, connection_name=db_conn.name,
                     payload={"tables": len(schema_data), "pii_columns": pii_count})
        return {
            "name": db_conn.name,
            "classifications": classifications
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Classification failed: {str(e)}")

@router.get("/{id}/drift-history")
def get_drift_history(id: int, db: Session = Depends(get_db)):
    """Return the last 5 schema snapshots with column-level drift events.

    Each snapshot now includes an optional ``drift_event`` key (``null`` if
    no drift was detected for that snapshot) with ``tables_added``,
    ``tables_removed``, ``columns_added``, ``columns_removed``, and
    ``type_changes`` — answering AC3's "what changed" without the caller
    re-diffing raw JSON blobs.
    """
    from app.models.schema_snapshot import SchemaSnapshot
    from app.models.drift_event import DriftEvent

    db_conn = _get_connection_or_404(id, db)
    snapshots = (
        db.query(SchemaSnapshot)
        .filter(SchemaSnapshot.connection_id == id)
        .order_by(SchemaSnapshot.captured_at.desc())
        .limit(5)
        .all()
    )
    snapshot_ids = [s.id for s in snapshots]

    # Bulk-load DriftEvents for all returned snapshots
    drift_events = {
        e.snapshot_id: e
        for e in db.query(DriftEvent)
        .filter(DriftEvent.snapshot_id.in_(snapshot_ids))
        .all()
    } if snapshot_ids else {}

    return {
        "connection": db_conn.name,
        "snapshots": [
            {
                "id": s.id,
                "schema_hash": s.schema_hash,
                "captured_at": s.captured_at,
                "table_count": len(s.schema_json) if s.schema_json else 0,
                "drift_event": (
                    {
                        "id": de.id,
                        "tables_added": de.tables_added,
                        "tables_removed": de.tables_removed,
                        "columns_added": de.columns_added,
                        "columns_removed": de.columns_removed,
                        "type_changes": de.type_changes,
                        "detected_at": de.detected_at,
                    }
                    if (de := drift_events.get(s.id))
                    else None
                ),
            }
            for s in snapshots
        ],
    }


@router.post("/{id}/rescan")
def rescan_connection(id: int, db: Session = Depends(get_db)):
    """On-demand schema drift re-scan for a single connection (FR6/AC3).

    Calls the same ``_check_single_connection_drift`` helper that the
    periodic Celery task uses for all connections, so the behaviour is
    identical.  Returns the drift result including the full diff, with
    column-level details, if drift was detected.
    """
    from app.tasks.ai_tasks import _check_single_connection_drift

    conn = _get_connection_or_404(id, db)
    result = _check_single_connection_drift(db, conn, actor="manual-rescan")
    db.commit()
    return result


def _build_drift_by_table(
    db: Session, connection_id: int, schema_tables: Dict[str, list],
) -> Dict[str, str]:
    """Query the latest DriftEvent for *connection_id* and return
    ``{table_name: "drifted" | "stable"}`` for every table present in the
    live schema.  Tables with no drift history at all are omitted (the
    frontend already degrades when the field is absent).
    """
    from app.models.drift_event import DriftEvent

    latest = (
        db.query(DriftEvent)
        .filter(DriftEvent.connection_id == connection_id)
        .order_by(DriftEvent.detected_at.desc())
        .first()
    )
    if latest is None:
        return {}

    result: Dict[str, str] = {}
    table_names = set(schema_tables.keys())
    drifted_set: set = set()

    for name in (latest.tables_added or []):
        if name in table_names:
            drifted_set.add(name)
    for name in (latest.tables_removed or []):
        if name in table_names:
            drifted_set.add(name)
    for entry in (latest.columns_added or []):
        if isinstance(entry, dict) and entry.get("table") in table_names:
            drifted_set.add(entry["table"])
    for entry in (latest.columns_removed or []):
        if isinstance(entry, dict) and entry.get("table") in table_names:
            drifted_set.add(entry["table"])
    for entry in (latest.type_changes or []):
        if isinstance(entry, dict) and entry.get("table") in table_names:
            drifted_set.add(entry["table"])

    for name in table_names:
        result[name] = "drifted" if name in drifted_set else "stable"
    return result


def _build_last_refresh_by_table(
    db: Session, connection_id: int, schema_tables: Dict[str, list],
) -> Dict[str, str]:
    """Return the latest persisted schema-scan time for each live table."""
    from app.models.schema_snapshot import SchemaSnapshot

    latest = (
        db.query(SchemaSnapshot)
        .filter(SchemaSnapshot.connection_id == connection_id)
        .order_by(SchemaSnapshot.captured_at.desc())
        .first()
    )
    if latest is None or latest.captured_at is None:
        return {}
    captured_at = latest.captured_at.isoformat()
    return {table_name: captured_at for table_name in schema_tables}


@router.get("/graph")
def get_graph_data(source_id: int, target_id: int, db: Session = Depends(get_db)):
    """
    Generate graph-structured data for Neo4j/NetworkX-style visualization.
    Combines schema metadata, diff results, security classifications, and AI matches.
    """
    source_conn = _get_connection_or_404(source_id, db)
    target_conn = _get_connection_or_404(target_id, db)

    try:
        source_schema = SchemaService.get_full_schema(source_conn)
        target_schema = SchemaService.get_full_schema(target_conn)

        # Diff (exact table-name matching)
        diff_result = DiffService.compare_schemas(source_schema, target_schema)

        # Real published Schema Mapper mapping between these connections, if
        # any — overrides false "not found" positives for legitimately
        # renamed tables (see _get_real_table_mappings docstring).
        real_mappings = _get_real_table_mappings(db, source_id, target_id)

        # Classifications for source and target (B04: symmetric classification)
        source_classifications = SecurityService.classify_schema(source_schema)
        target_classifications = SecurityService.classify_schema(target_schema)

        # Drift status per table (B02: drift_status node indicator)
        source_drift_by_table = _build_drift_by_table(db, source_conn.id, source_schema)
        target_drift_by_table = _build_drift_by_table(db, target_conn.id, target_schema)
        source_last_refresh = _build_last_refresh_by_table(db, source_conn.id, source_schema)
        target_last_refresh = _build_last_refresh_by_table(db, target_conn.id, target_schema)

        # AI matches: only attempted for a table pair that's genuinely
        # unmapped after considering both exact-name matches AND real
        # published mappings — previously this ran unconditionally against
        # the first source/first target table regardless of whether they
        # were already matched, and always drew its edge between those two
        # nodes regardless of which tables the AI actually compared.
        ai_matches_by_pair = []
        matched_source_tables = set(diff_result.get("matched_tables", [])) | set(real_mappings.keys())
        matched_target_names = set(diff_result.get("matched_tables", [])) | {
            info["target_table"]
            for infos in real_mappings.values()
            for info in infos
        }
        unmapped_source = [t for t in source_schema if t not in matched_source_tables]
        unmapped_target = [t for t in target_schema if t not in matched_target_names]
        available_targets = set(unmapped_target)
        for source_table in unmapped_source:
            best_pair = None
            for target_table in sorted(available_targets):
                result = AIService.match_schemas(
                    source_name=source_table,
                    source_schema=source_schema[source_table],
                    target_name=target_table,
                    target_schema=target_schema[target_table],
                    allow_llm=False,
                )
                matches = [
                    m for m in result.get("matches", [])
                    if m.get("target") and m.get("confidence", 0) >= 30
                ]
                score = sum(float(m.get("confidence", 0)) for m in matches) / len(matches) if matches else 0
                if matches and (best_pair is None or score > best_pair[0]):
                    best_pair = (score, target_table, matches)
            if best_pair is not None:
                _, target_table, matches = best_pair
                available_targets.remove(target_table)
                ai_matches_by_pair.append({
                    "source_table": source_table,
                    "target_table": target_table,
                    "matches": matches,
                })

        graph = DiffService.generate_graph_data(
            source_schema=source_schema,
            target_schema=target_schema,
            diff_result=diff_result,
            classifications=source_classifications,
            ai_matches=[],
            source_name=source_conn.name,
            target_name=target_conn.name,
            real_mappings=real_mappings,
            ai_match_pair=None,
            ai_matches_by_pair=ai_matches_by_pair,
            classifications_target=target_classifications,
            drift_by_table=source_drift_by_table,
            drift_by_table_target=target_drift_by_table,
            last_refresh_by_table=source_last_refresh,
            last_refresh_by_table_target=target_last_refresh,
        )
        return graph
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph generation failed: {str(e)}")
