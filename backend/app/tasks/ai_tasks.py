"""Celery tasks wrapping synchronous AI / schema-mapping services.

Each task accepts only serializable inputs (ints, strings, dicts, JSON strings)
and creates its own short-lived DB session via SessionLocal.
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.connection import DBConnection
from app.services.ai_service import AIService
from app.services.nl2sql_service import NL2SQLService
from app.services.schema_mapper_service import SchemaMapperService
from app.services.schema_service import SchemaService

logger = logging.getLogger(__name__)


def _get_schema_for_connection(connection_id: int) -> Dict[str, Any]:
    """Helper: load a DBConnection by id, fetch its schema, close the session."""
    db = SessionLocal()
    try:
        conn = db.query(DBConnection).filter(DBConnection.id == connection_id).first()
        if conn is None:
            raise ValueError(f"DBConnection id={connection_id} not found")
        return SchemaService.get_full_schema(conn)
    finally:
        db.close()


@celery_app.task(name="app.tasks.ai_tasks.match_schemas_task", bind=True)
def match_schemas_task(
    self,
    source_id: int,
    target_id: int,
    source_table: str,
    target_table: str,
) -> Dict[str, Any]:
    """Async wrapper around AIService.match_schemas."""
    db = SessionLocal()
    try:
        source_conn = db.query(DBConnection).filter(DBConnection.id == source_id).first()
        target_conn = db.query(DBConnection).filter(DBConnection.id == target_id).first()

        if not source_conn or not target_conn:
            raise ValueError(
                f"Connection not found (source_id={source_id}, target_id={target_id})"
            )

        source_schema = SchemaService.get_full_schema(source_conn)
        target_schema = SchemaService.get_full_schema(target_conn)

        if source_table not in source_schema:
            raise ValueError(
                f"Table '{source_table}' not found in source '{source_conn.name}'"
            )
        if target_table not in target_schema:
            raise ValueError(
                f"Table '{target_table}' not found in target '{target_conn.name}'"
            )

        match_results = AIService.match_schemas(
            source_name=source_table,
            source_schema=source_schema[source_table],
            target_name=target_table,
            target_schema=target_schema[target_table],
        )
        return {
            "source": source_conn.name,
            "target": target_conn.name,
            "source_table": source_table,
            "target_table": target_table,
            "suggestions": match_results,
        }
    finally:
        db.close()


@celery_app.task(name="app.tasks.ai_tasks.nl2sql_task", bind=True)
def nl2sql_task(
    self,
    connection_id: int,
    question: str,
    execute: bool = False,
) -> Dict[str, Any]:
    """Async wrapper around NL2SQLService.generate_sql.

    `execute` is accepted for API compatibility but execution is intentionally
    out of scope for the async worker (callers should run the returned SQL
    through the synchronous /api/v1/query endpoint).
    """
    db = SessionLocal()
    try:
        conn = db.query(DBConnection).filter(DBConnection.id == connection_id).first()
        if conn is None:
            raise ValueError(f"DBConnection id={connection_id} not found")

        schema_context = SchemaService.get_full_schema(conn)
        sql_result = NL2SQLService.generate_sql(
            natural_query=question,
            schema_context=schema_context,
            db_type=conn.type,
        )

        return {
            "connection": conn.name,
            "question": question,
            "sql_result": sql_result,
        }
    finally:
        db.close()


@celery_app.task(name="app.tasks.ai_tasks.generate_migration_task", bind=True)
def generate_migration_task(
    self,
    mappings_json: str,
    target_db_type: str = "sqlite",
) -> Dict[str, Any]:
    """Async wrapper around SchemaMapperService.generate_migration_sql.

    `mappings_json` is a JSON string so the task stays serializable.
    """
    db = SessionLocal()
    try:
        mappings: List[Dict[str, Any]] = json.loads(mappings_json) if mappings_json else []
        if not isinstance(mappings, list):
            raise ValueError("mappings_json must decode to a list of mapping rules")

        result = SchemaMapperService.generate_migration_sql(
            mappings=mappings,
            target_db_type=target_db_type,
        )
        result["target_db_type"] = target_db_type
        return result
    finally:
        db.close()


@celery_app.task(name="app.tasks.ai_tasks.parse_english_mapping_task", bind=True)
def parse_english_mapping_task(
    self,
    text: str,
    source_schema_json: str,
    target_schema_json: str,
) -> Dict[str, Any]:
    """Async wrapper around SchemaMapperService.parse_english_mapping.

    `source_schema_json` and `target_schema_json` are JSON strings so the
    task only accepts serializable inputs.
    """
    db = SessionLocal()
    try:
        source_schema = json.loads(source_schema_json) if source_schema_json else {}
        target_schema = json.loads(target_schema_json) if target_schema_json else {}

        result = SchemaMapperService.parse_english_mapping(
            text=text,
            source_schema=source_schema,
            target_schema=target_schema,
        )
        return result
    finally:
        db.close()


@celery_app.task(name="app.tasks.ai_tasks.schema_wide_match_task", bind=True)
def schema_wide_match_task(
    self,
    source_id: int,
    target_id: int,
) -> Dict[str, Any]:
    """Schema-wide matcher: iterate ALL source tables against ALL target tables.

    For each source table, score every target table by the maximum column-level
    confidence returned by ``AIService.match_schemas`` and pick the target
    with the highest score. Pairs with confidence >= 50 are considered matched;
    otherwise the source table is reported as unmatched. A target table is
    listed as unmatched only when no source table picked it as its best match.
    """
    if source_id == target_id:
        raise ValueError("Source and target must be different databases")

    db = SessionLocal()
    try:
        source_conn = db.query(DBConnection).filter(DBConnection.id == source_id).first()
        target_conn = db.query(DBConnection).filter(DBConnection.id == target_id).first()

        if not source_conn or not target_conn:
            raise ValueError("Source or Target Connection not found")

        source_schema = SchemaService.get_full_schema(source_conn)
        target_schema = SchemaService.get_full_schema(target_conn)

        # Graceful handling: if either side has no tables, return empty results
        # rather than raising — callers can still render the empty state.
        if not source_schema or not target_schema:
            return {
                "source": source_conn.name,
                "target": target_conn.name,
                "table_mappings": [],
                "unmatched_source": list(source_schema.keys()) if source_schema else [],
                "unmatched_target": list(target_schema.keys()) if target_schema else [],
                "total_source_tables": len(source_schema) if source_schema else 0,
                "total_target_tables": len(target_schema) if target_schema else 0,
            }

        matched_target_tables: set = set()
        table_mappings: List[Dict[str, Any]] = []
        unmatched_source: List[str] = []

        for src_table, src_cols in source_schema.items():
            best_target_table = None
            best_details: Dict[str, Any] = {}
            best_confidence: float = 0.0

            for tgt_table, tgt_cols in target_schema.items():
                match_result = AIService.match_schemas(
                    source_name=src_table,
                    source_schema=src_cols,
                    target_name=tgt_table,
                    target_schema=tgt_cols,
                )
                matches = match_result.get("matches", []) or []
                if not matches:
                    continue
                max_conf = max(
                    (m.get("confidence", 0) or 0 for m in matches),
                    default=0,
                )
                if max_conf > best_confidence:
                    best_confidence = max_conf
                    best_target_table = tgt_table
                    best_details = match_result

            if best_target_table is not None and best_confidence >= 50:
                table_mappings.append(
                    {
                        "source_table": src_table,
                        "target_table": best_target_table,
                        "confidence": best_confidence,
                        "details": best_details,
                    }
                )
                matched_target_tables.add(best_target_table)
            else:
                unmatched_source.append(src_table)

        unmatched_target = [
            t for t in target_schema.keys() if t not in matched_target_tables
        ]

        return {
            "source": source_conn.name,
            "target": target_conn.name,
            "table_mappings": table_mappings,
            "unmatched_source": unmatched_source,
            "unmatched_target": unmatched_target,
            "total_source_tables": len(source_schema),
            "total_target_tables": len(target_schema),
        }
    finally:
        db.close()


def _check_single_connection_drift(
    db: SessionLocal, conn: DBConnection, actor: str = "drift-monitor"
) -> Dict[str, Any]:
    """Check & persist drift for a single connection.

    Shared helper called both by the periodic Celery task (all connections)
    and by the on-demand ``POST /api/v1/schema/{id}/rescan`` endpoint (one
    connection).  Returns a dict with connection name, whether drift was
    detected, and the computed diff (if any).

    Side-effects (all within *caller's* transaction — no commit here):
        - Creates a new ``SchemaSnapshot`` row.
        - If drift is detected, creates a ``DriftEvent`` row AND an
          ``AuditLog`` row.
        - Prunes old snapshots (keeps last 10 per connection).
    """
    from app.models.schema_snapshot import SchemaSnapshot
    from app.models.drift_event import DriftEvent
    from app.models.audit import AuditLog
    from app.services.diff_service import DiffService

    result: Dict[str, Any] = {"connection": conn.name, "drift": False}
    try:
        schema = SchemaService.get_full_schema(conn)
    except Exception as exc:
        logger.warning("Drift check: schema fetch failed for '%s': %s", conn.name, exc)
        result["error"] = str(exc)
        return result

    schema_str = json.dumps(schema, sort_keys=True, default=str)
    current_hash = hashlib.sha256(schema_str.encode()).hexdigest()

    latest = (
        db.query(SchemaSnapshot)
        .filter(SchemaSnapshot.connection_id == conn.id)
        .order_by(SchemaSnapshot.captured_at.desc())
        .first()
    )

    previous_snapshot_id = None
    if latest is not None:
        previous_snapshot_id = latest.id

    # Save new snapshot FIRST so we can reference its id in the DriftEvent
    snapshot = SchemaSnapshot(
        connection_id=conn.id,
        connection_name=conn.name,
        schema_hash=current_hash,
        schema_json=schema,
    )
    db.add(snapshot)
    db.flush()  # assign snapshot.id before we reference it below

    if latest is None:
        logger.info("Drift check: first snapshot for '%s'", conn.name)
    elif latest.schema_hash != current_hash:
        result["drift"] = True
        diff = DiffService.compare_schemas(latest.schema_json or {}, schema)
        table_diffs = diff.get("table_diffs", {})

        # ── Build column-level change lists from the computed table_diffs ────
        columns_added: list = []
        columns_removed: list = []
        type_changes: list = []

        for table, td in table_diffs.items():
            # compare_tables(source=old_snapshot, target=new_live_schema):
            #   missing_in_target = in old but not in new = REMOVED
            #   missing_in_source  = in new but not in old = ADDED
            for col_name in td.get("missing_in_source", []):
                columns_added.append({"table": table, "column": col_name})
            for col_name in td.get("missing_in_target", []):
                columns_removed.append({"table": table, "column": col_name})
            for tm in td.get("type_mismatches", []):
                type_changes.append({
                    "table": table,
                    "column": tm["column"],
                    "old_type": tm["source_type"],
                    "new_type": tm["target_type"],
                })

        # DiffService.compare_schemas(old_snapshot, new_live_schema):
        #   missing_tables_in_target = in old but not in new = REMOVED
        #   missing_tables_in_source  = in new but not in old = ADDED
        #   compare_tables: missing_in_target = in old but not in new = REMOVED
        #                  missing_in_source  = in new but not in old = ADDED
        tables_removed = diff.get("missing_tables_in_target", [])
        tables_added = diff.get("missing_tables_in_source", [])

        # Persist DriftEvent — points to the NEW snapshot (the one that
        # detected the drift), with previous_snapshot_id referencing the old.
        db.add(DriftEvent(
            connection_id=conn.id,
            snapshot_id=snapshot.id,
            previous_snapshot_id=previous_snapshot_id,
            tables_added=tables_added,
            tables_removed=tables_removed,
            columns_added=columns_added,
            columns_removed=columns_removed,
            type_changes=type_changes,
        ))

        # Persist AuditLog (existing behaviour preserved)
        audit = AuditLog(
            event_type="schema_drift_detected",
            actor=actor,
            connection_id=conn.id,
            connection_name=conn.name,
            payload={
                "previous_hash": latest.schema_hash,
                "current_hash": current_hash,
                "diff_summary": {
                    "matched_tables": len(diff.get("matched_tables", [])),
                    "tables_added": tables_added,
                    "tables_removed": tables_removed,
                    "columns_added": len(columns_added),
                    "columns_removed": len(columns_removed),
                    "type_changes": len(type_changes),
                },
            },
            status="warning",
        )
        db.add(audit)
        logger.warning(
            "Schema drift detected for '%s': +%d tables, -%d tables, "
            "+%d columns, -%d columns, %d type changes",
            conn.name,
            len(tables_added), len(tables_removed),
            len(columns_added), len(columns_removed),
            len(type_changes),
        )
        result["diff"] = diff

    # Keep only last 10 snapshots per connection
    old_snapshots = (
        db.query(SchemaSnapshot)
        .filter(SchemaSnapshot.connection_id == conn.id)
        .order_by(SchemaSnapshot.captured_at.desc())
        .offset(10)
        .all()
    )
    for old in old_snapshots:
        db.delete(old)

    return result


@celery_app.task(name="app.tasks.ai_tasks.check_schema_drift_task", bind=True)
def check_schema_drift_task(self) -> Dict[str, Any]:
    """Periodic task: snapshot all DB schemas and detect drift."""
    db = SessionLocal()
    results = []
    try:
        connections = (
            db.query(DBConnection)
            .filter(DBConnection.is_deleted == False)  # noqa: E712
            .all()
        )
        for conn in connections:
            r = _check_single_connection_drift(db, conn)
            results.append(r)
        db.commit()
    except Exception as exc:
        logger.error("check_schema_drift_task failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()

    drifted = sum(1 for r in results if r.get("drift"))
    if drifted:
        # Autopilot ≤10s NFR: evaluate triggers now instead of waiting for
        # the beat sweep. Never let this break the drift task itself.
        try:
            from app.tasks.autopilot_tasks import evaluate_recommendations_task
            evaluate_recommendations_task.delay()
        except Exception as exc:
            logger.warning("autopilot evaluate dispatch after drift failed: %s", exc)

    return {"checked": len(results), "drifted": drifted}


@celery_app.task(name="app.tasks.ai_tasks.run_autopilot_task", bind=True)
def run_autopilot_task(self, run_id: str, source_id: int, target_id: int, mode: str) -> Dict[str, Any]:
    """Multi-step autonomous agent loop for AI Autopilot."""
    from app.models.autopilot import AutopilotRun, AutopilotLog
    from app.models.audit import AuditLog
    from app.services.diff_service import DiffService
    from app.services.security_service import SecurityService
    from app.services.schema_mapper_service import SchemaMapperService
    from app.services.pipeline_service import PipelineService

    db = SessionLocal()

    def _log(step: str, message: str, level: str = "info") -> None:
        try:
            db.add(AutopilotLog(run_id=run_id, step=step, message=message, level=level))
            db.commit()
        except Exception as exc:
            logger.warning("Failed to write autopilot log: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass

    def _finish(status: str, summary: Dict[str, Any]) -> None:
        try:
            run = db.query(AutopilotRun).filter(AutopilotRun.id == run_id).first()
            if run:
                run.status = status
                run.completed_at = datetime.now(timezone.utc)
                run.result_summary = summary
                db.add(AuditLog(
                    event_type="autopilot_run",
                    actor="autopilot",
                    connection_id=source_id,
                    payload={"run_id": run_id, "mode": mode, "status": status, **summary},
                    status="success" if status == "completed" else "failure",
                ))
                db.commit()
        except Exception as exc:
            logger.warning("Failed to update autopilot run status: %s", exc)

    try:
        _log("init", "Autopilot initialized — scanning source and target schemas")

        source_conn = db.query(DBConnection).filter(DBConnection.id == source_id).first()
        target_conn = db.query(DBConnection).filter(DBConnection.id == target_id).first()
        if not source_conn or not target_conn:
            _log("init", f"Connection not found (source_id={source_id}, target_id={target_id})", "error")
            _finish("failed", {"error": "Connection not found"})
            return {"status": "failed"}

        # Step 1: Extract schemas
        _log("schema", f"Extracting schema from '{source_conn.name}'...")
        source_schema = SchemaService.get_full_schema(source_conn)
        _log("schema", f"Source '{source_conn.name}': {len(source_schema)} tables found")

        _log("schema", f"Extracting schema from '{target_conn.name}'...")
        target_schema = SchemaService.get_full_schema(target_conn)
        _log("schema", f"Target '{target_conn.name}': {len(target_schema)} tables found")

        # Step 2: AI matching
        _log("matching", "Running AI semantic column matching across all table pairs...")
        table_mappings: List[Dict[str, Any]] = []
        unmatched_source: List[str] = []
        matched_target_tables: set = set()

        for src_table, src_cols in source_schema.items():
            best_target = None
            best_conf = 0.0
            best_details: Dict[str, Any] = {}
            for tgt_table, tgt_cols in target_schema.items():
                try:
                    match_result = AIService.match_schemas(
                        source_name=src_table, source_schema=src_cols,
                        target_name=tgt_table, target_schema=tgt_cols,
                    )
                    matches = match_result.get("matches", []) or []
                    max_conf = max((m.get("confidence", 0) for m in matches), default=0)
                    if max_conf > best_conf:
                        best_conf = max_conf
                        best_target = tgt_table
                        best_details = match_result
                except Exception:
                    pass
            if best_target and best_conf >= 50:
                table_mappings.append({"source_table": src_table, "target_table": best_target,
                                       "confidence": best_conf, "details": best_details})
                matched_target_tables.add(best_target)
                _log("matching", f"Matched '{src_table}' → '{best_target}' ({best_conf:.0f}% confidence)")
            else:
                unmatched_source.append(src_table)
                _log("matching", f"No match found for '{src_table}' (best confidence: {best_conf:.0f}%)", "warning")

        unmatched_target = [t for t in target_schema if t not in matched_target_tables]
        _log("matching", f"AI matching complete: {len(table_mappings)} matched, {len(unmatched_source)} unmatched source tables")

        # Step 3: Schema diff
        _log("diff", "Running structural schema diff...")
        diff = DiffService.compare_schemas(source_schema, target_schema)
        missing_in_target = diff.get("missing_tables_in_target", [])
        type_mismatches_count = sum(len(td.get("type_mismatches", [])) for td in diff.get("table_diffs", {}).values())
        _log("diff", f"Diff: {len(missing_in_target)} tables missing in target, {type_mismatches_count} type mismatches")

        # Step 4: PII classification
        _log("security", f"Running PII classification on '{source_conn.name}'...")
        classifications = SecurityService.classify_schema(source_schema)
        pii_count = sum(
            1 for cols in classifications.values()
            for c in cols
            if isinstance(c, dict) and c.get("classification", {}).get("level") == "High"
        )
        _log("security", f"Security scan complete: {pii_count} PII columns detected")

        # Step 5: Generate migration SQL
        _log("sql", "Generating migration SQL from matched table mappings...")
        mapping_rules: List[Dict[str, Any]] = []
        for tm in table_mappings:
            for match in tm["details"].get("matches", []):
                if match.get("confidence", 0) >= 50:
                    mapping_rules.append({
                        "action": "column_mapping",
                        "source_table": tm["source_table"],
                        "target_table": tm["target_table"],
                        "source_column": match["source"],
                        "target_column": match["target"],
                    })
        sql_result = SchemaMapperService.generate_migration_sql(mapping_rules, target_conn.type)
        ddl_count = len(sql_result.get("ddl", []))
        dml_count = len(sql_result.get("dml", []))
        _log("sql", f"Generated {ddl_count} DDL and {dml_count} DML statements")

        # Step 6: Execute if mode == "execute"
        rows_copied = 0
        if mode == "execute" and table_mappings:
            _log("execute", "Executing pipeline migration...")
            try:
                nodes = [
                    {"id": "src", "type": "source", "config": {"connection_id": source_id}},
                    {"id": "matcher", "type": "ai_matcher", "config": None},
                    {"id": "tgt", "type": "target", "config": {"connection_id": target_id}},
                ]
                edges = [
                    {"id": "e1", "source": "src", "target": "matcher"},
                    {"id": "e2", "source": "matcher", "target": "tgt"},
                ]
                exec_result = PipelineService.execute_pipeline(nodes, edges)
                rows_copied = exec_result.get("rows_copied", 0)
                _log("execute", f"Pipeline execution complete — {rows_copied} rows copied")
            except Exception as exc:
                _log("execute", f"Pipeline execution failed: {exc}", "error")
        else:
            _log("execute", "Mode is 'suggest' — skipping live execution. Review the generated SQL before applying.")

        summary = {
            "source": source_conn.name,
            "target": target_conn.name,
            "tables_matched": len(table_mappings),
            "tables_unmatched": len(unmatched_source),
            "pii_columns": pii_count,
            "ddl_statements": ddl_count,
            "dml_statements": dml_count,
            "rows_copied": rows_copied,
            "table_mappings": table_mappings,
            "migration_sql": sql_result,
        }
        _log("complete", f"Autopilot run complete. {len(table_mappings)} tables matched, {pii_count} PII columns identified.")
        _finish("completed", summary)
        return summary

    except Exception as exc:
        logger.error("run_autopilot_task failed: %s", exc)
        _log("error", f"Autopilot failed: {exc}", "error")
        _finish("failed", {"error": str(exc)})
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "failed", "error": str(exc)}
    finally:
        db.close()
