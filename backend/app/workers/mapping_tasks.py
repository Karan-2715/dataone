"""Celery task for AI mapping suggestions (Schema Mapper upgrade).

Performance fix (review §11.5): the prior implementation called
``AIService.match_schemas(target_schema=tgt_cols, ...)`` once per
**target column** — making the total LLM-call cost
``Σ(target_columns) × Σ(source_tables)`` instead of
``Σ(target_tables) × Σ(source_tables)``. At the TRD's own target scale
(50 tables / 1,000 columns) this is ~1000× more LLM calls than necessary
and blows past the 3-second p95 NFR.

This rewrite hoists the ``match_schemas`` call outside the per-column
loop: one call per (source_table, target_table) pair, then the resulting
matches are distributed to their respective unmapped target columns.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.connection import DBConnection
from app.models.mapping import AISuggestion, Mapping
from app.services.ai_service import AIService
from app.services.audit_helper import record_audit
from app.services.mapping_feedback_service import HistoricalMatchService
from app.services.schema_service import SchemaService
from app.services.transformation_proposer import propose_transformations

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.workers.mapping_tasks.suggest_mappings_task",
    bind=True,
)
def suggest_mappings_task(self, mapping_id: int) -> Dict[str, Any]:
    """Walk unmapped target columns and create AI suggestions for each.

    Algorithm:
      For each target table T:
        U = unmapped target columns in T without a pending suggestion
        if U is empty: skip
        best_by_col = {}
        For each source table S:
          result = AIService.match_schemas(S, T, all unmapped cols in U)
          For each match in result.matches where match.target in U:
            keep the highest-confidence match per target column
        For each target column with best match (confidence >= 50):
          create AISuggestion row
    """
    db = SessionLocal()
    try:
        m = db.query(Mapping).filter(Mapping.id == mapping_id).first()
        if not m:
            return {"status": "failed", "error": "mapping not found"}

        source_conn = (
            db.query(DBConnection).filter(DBConnection.id == m.source_id).first()
        )
        target_conn = (
            db.query(DBConnection).filter(DBConnection.id == m.target_id).first()
        )
        if not source_conn or not target_conn:
            return {"status": "failed", "error": "connection not found"}

        try:
            source_schema = SchemaService.get_full_schema(source_conn)
            target_schema = SchemaService.get_full_schema(target_conn)
            
            # OPTIMIZATION: The frontend UI encodes the mapping as:
            # "Map {source_catalog}.{source_schema}.{table} → {target_catalog}.{target_schema}"
            # We parse this to filter source to one table, and target to the selected schema.
            if m.name and m.name.startswith("Map "):
                rest = m.name[4:]  # e.g. "cat.schema.table → tgt_cat.tgt_schema"
                
                if " → " in rest:
                    specific_source_table, target_schema_prefix = rest.split(" → ", 1)
                    specific_source_table = specific_source_table.strip()
                    target_schema_prefix = target_schema_prefix.strip()  # e.g. "cat.schema"
                else:
                    specific_source_table = rest.strip()
                    target_schema_prefix = None
                
                # Filter source schema to just the one selected table
                if specific_source_table in source_schema:
                    source_schema = {specific_source_table: source_schema[specific_source_table]}
                    logger.info("Filtered source schema to specific table: %s", specific_source_table)
                
                # Filter target schema to only tables in the selected target catalog.schema
                if target_schema_prefix:
                    filtered_target = {
                        table_name: cols
                        for table_name, cols in target_schema.items()
                        if table_name.startswith(target_schema_prefix + ".")
                    }
                    if filtered_target:
                        target_schema = filtered_target
                        logger.info("Filtered target schema to prefix '%s': %d tables", target_schema_prefix, len(target_schema))
                    else:
                        logger.warning("No target tables found with prefix '%s', using all target tables", target_schema_prefix)
                
        except Exception as exc:
            logger.warning(
                "suggest_mappings_task: schema fetch failed for mapping %s: %s",
                mapping_id, exc,
            )
            return {"status": "failed", "error": f"schema fetch: {exc}"}

        # Skip target columns that are already mapped in the current draft.
        existing_targets = {
            (e.target_table, e.target_column)
            for e in (m.edges or [])
            if e.version_id is None
        }

        # Prior suggestions constrain what a re-run may propose: a pending
        # suggestion is an open question the user hasn't answered yet —
        # re-running must not duplicate it — and a rejected (source→target)
        # pair is a decision already made — never offer the same match
        # again (a *different* source for that target is still fair game).
        prior = (
            db.query(AISuggestion)
            .filter(AISuggestion.mapping_id == m.id)
            .all()
        )
        pending_targets = {
            (s.target_table, s.target_column)
            for s in prior if s.status == "pending"
        }
        rejected_pairs = {
            (s.source_table, s.source_column, s.target_table, s.target_column)
            for s in prior if s.status == "rejected"
        }

        # Pass schemas through the new ReMatchEngine
        from app.services.rematch_engine import ReMatchEngine
        # We fetch the owner's LLM preference if available
        from app.models.user import User
        user_model = None
        if m.created_by:
            mapping_owner = db.query(User).filter(User.email == m.created_by).first()
            if mapping_owner and mapping_owner.llm_model:
                user_model = mapping_owner.llm_model
                logger.info("Using owner's selected LLM model: %s", user_model)
        
        # Databricks Apps don't have DATABRICKS_WORKSPACE_URL set globally.
        # But we can extract it from the source Databricks connection config!
        workspace_url = f"https://{source_conn.config.get('server_hostname', '')}" if source_conn.config.get('server_hostname') else None
        access_token = source_conn.config.get('access_token')

        # But we'll let the engine fall back to default if not provided
        engine = ReMatchEngine(
            db, 
            user_llm_model=user_model,
            workspace_url=workspace_url,
            access_token=access_token
        )
        
        # We only pass unmapped target columns to avoid embedding/matching everything
        filtered_target_schema = {}
        for tgt_table, tgt_cols in target_schema.items():
            unmapped_cols = [
                c for c in tgt_cols
                if (tgt_table, c.get("name")) not in existing_targets
                and (tgt_table, c.get("name")) not in pending_targets
            ]
            if unmapped_cols:
                filtered_target_schema[tgt_table] = unmapped_cols

        best_by_col: Dict[tuple, Dict[str, Any]] = {}
        
        if filtered_target_schema:
            try:
                rematch_mappings = engine.map_schemas(source_schema, filtered_target_schema)
                for match in rematch_mappings:
                    tgt_name = match["target_column"]
                    src_table = match["source_table"]
                    tgt_table = match["target_table"]
                    
                    if (src_table, match["source_column"], tgt_table, tgt_name) in rejected_pairs:
                        continue
                        
                    # ReMatchEngine returns 0.0-1.0, convert to 0-100 for AISuggestion
                    conf = match["confidence_score"] * 100
                    
                    existing_best = best_by_col.get((tgt_table, tgt_name))
                    if existing_best is None or conf > existing_best["confidence"]:
                        # Find source type
                        src_type = None
                        for c in source_schema.get(src_table, []):
                            if c["name"] == match["source_column"]:
                                src_type = c.get("type")
                                break
                                
                        best_by_col[(tgt_table, tgt_name)] = {
                            "source_table": src_table,
                            "source_column": match["source_column"],
                            "source_type": src_type,
                            "confidence": conf,
                            "reason": match["reasoning"],
                            "components": {
                                "semantic_score": conf,
                                "type_compatible": match["data_type_compatible"]
                            }
                        }
            except Exception as exc:
                logger.error("ReMatchEngine failed: %s", exc)
                # Persist the error so the frontend polling loop stops and displays it
                error_sugg = AISuggestion(
                    mapping_id=m.id,
                    target_table="ERROR",
                    target_column="ERROR",
                    source_table=list(source_schema.keys())[0] if source_schema else "ERROR",
                    source_column="ERROR",
                    confidence=0.0,
                    reason=f"Databricks AI API Error: {str(exc)}",
                    status="error"
                )
                db.add(error_sugg)
                db.commit()
                return {"status": "error", "mapping_id": mapping_id, "error": str(exc)}

        suggestions_created = 0

        for tgt_table, tgt_cols in target_schema.items():
            unmapped_cols = [
                c for c in tgt_cols
                if (tgt_table, c.get("name")) not in existing_targets
                and (tgt_table, c.get("name")) not in pending_targets
            ]

            # Materialize one AISuggestion per target column with a best match
            # above the confidence threshold.
            for tgt_col in unmapped_cols:
                best = best_by_col.get((tgt_table, tgt_col.get("name")))
                if not best or best["confidence"] < 50:
                    continue
                proposed, _ = propose_transformations(
                    db,
                    m.source_id,
                    [{
                        "name": tgt_table,
                        "columns": [{
                            "name": tgt_col["name"],
                            "type": tgt_col.get("type"),
                            "nullable": tgt_col.get("nullable", True),
                            "source_refs": [{
                                "table": best["source_table"],
                                "column": best["source_column"],
                                "type": best["source_type"],
                            }],
                        }],
                    }],
                )
                proposal = proposed[0] if proposed else {}

                # E14-2/3: nudge ranking with heuristic memory — has this
                # exact (source_column, target_column) name pair been
                # decided before, anywhere on the platform? Exposed as a
                # confidence contributor alongside name/type/semantic.
                components = dict(best["components"] or {})
                historical = HistoricalMatchService.lookup(
                    db, best["source_column"], tgt_col["name"],
                )
                components["historical_mapping"] = historical["score"]
                confidence = best["confidence"]
                if historical["accepted_count"] > 0:
                    confidence = min(100.0, confidence + round(historical["score"] * 0.15, 1))

                db.add(AISuggestion(
                    mapping_id=m.id,
                    target_table=tgt_table,
                    target_column=tgt_col["name"],
                    target_type=tgt_col.get("type"),
                    source_table=best["source_table"],
                    source_column=best["source_column"],
                    source_type=best["source_type"],
                    confidence=confidence,
                    reason=best["reason"],
                    components=components,
                    suggested_transformation=proposal.get("transformation"),
                    transformation_note=proposal.get("note"),
                    status="pending",
                ))
                suggestions_created += 1

        if suggestions_created == 0:
            db.add(AISuggestion(
                mapping_id=m.id,
                target_table="ERROR",
                target_column="ERROR",
                target_type="ERROR",
                source_table="ERROR",
                source_column="ERROR",
                source_type="ERROR",
                confidence=0.0,
                reason="Schemas are completely different or the AI found no matching columns.",
                status="error",
            ))
            suggestions_created += 1

        db.flush()
        record_audit(
            db, "mapping_suggestions_ready",
            actor="mapping-suggester",
            connection_id=m.source_id,
            payload={
                "mapping_id": m.id,
                "suggestions_created": suggestions_created,
            },
        )
        db.commit()

        return {
            "status": "completed",
            "mapping_id": m.id,
            "suggestions_created": suggestions_created,
        }
    except Exception as exc:
        logger.error("suggest_mappings_task failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "failed", "error": str(exc)}
    finally:
        # Always release request_suggestions' idempotency guard, regardless
        # of which return/exception path above was taken (mapping not found,
        # connection not found, schema fetch failure, an unexpected
        # exception, or a clean completion) — a query-by-id update rather
        # than mutating the `m` loaded above, since some of those early
        # returns happen before `m` even exists. Otherwise a failed run
        # would permanently wedge this mapping's "AI Suggest" button
        # believing a task is still running forever.
        try:
            db.query(Mapping).filter(Mapping.id == mapping_id).update(
                {"pending_suggestion_task_id": None}
            )
            db.commit()
        except Exception as exc:
            logger.warning(
                "suggest_mappings_task: failed to clear pending_suggestion_task_id "
                "for mapping %s: %s", mapping_id, exc,
            )
        db.close()
