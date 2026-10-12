"""Pipeline Service.

Two responsibilities coexist in this module under the Task #1 design:

  1. Legacy synchronous graph executor (`execute_pipeline` + helpers).
     Pre-dates the TRD; stateless; AI-matcher driven. **Will be replaced
     by Task #3** (execution engine that consumes published mapping
     versions). Kept verbatim (restored in Bug #12 after the Task #1
     refactor accidentally gutted it) so Task #3 can land as a clean swap.

  2. CRUD surface for the persistent `Pipeline` / `PipelineRun` models
     added in Task #1: `create_pipeline`, `get_pipeline`, `list_pipelines`,
     `update_pipeline`, `delete_pipeline`, `list_runs`. Mirrors the
     `MappingService` pattern from the Schema Mapper upgrade.

Task #2 adds `compute_schema_hash` and `PipelineCRUD.validate_drift` for
the FR2 / AC2 pre-run drift check (hardened by bugs #15–#18: column-order
normalization, fail-closed on missing baseline, column-level drift in
`changed_tables`). Task #3 will call validate_drift at the entrypoint of
execute (manual and scheduled); the same method is exposed via
GET /pipelines/{id}/drift so users can preview before running.
"""

import hashlib
import json
import logging
import os
import sqlite3
from collections import deque
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.connection import DBConnection
from app.models.mapping import Mapping, MappingVersion
from app.models.pipeline import Pipeline, PipelineRun, RetryPolicy, Schedule
from app.services.ai_service import AIService
from app.services.audit_helper import record_audit
from app.services.schema_mapper_service import SchemaMapperService
from app.services.schema_service import SchemaService

ACTIVE_RUN_STATUSES = ("pending", "running", "retrying")

logger = logging.getLogger(__name__)


PIPELINE_NODE_TYPES = {"source", "ai_matcher", "target"}
CONFIDENCE_THRESHOLD = 50


# ── Schema normalization + hash (Task #2, bugs #15/#16) ─────────────

def _normalize_schema(schema: Any) -> Any:
    """Recursively normalize a schema structure so that logically equal
    schemas serialize identically: dict keys are handled by
    ``json.dumps(sort_keys=True)``; lists (e.g. a table's columns, which
    connectors may return in varying order between calls) are sorted by
    their canonical JSON serialization. Duplicate items are preserved,
    so multiset differences still register as drift."""
    if isinstance(schema, dict):
        return {k: _normalize_schema(v) for k, v in schema.items()}
    if isinstance(schema, list):
        normalized = [_normalize_schema(item) for item in schema]
        return sorted(
            normalized,
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        )
    return schema


def compute_schema_hash(schema: Dict[str, Any]) -> str:
    """Canonical SHA-256 of a normalized schema dict.

    Order-independent for both dict keys and nested lists (columns), so
    two schemas with the same content always produce the same hash and
    ``baseline_hash != current_hash`` is exactly equivalent to drift.
    """
    normalized = json.dumps(
        _normalize_schema(schema), sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class PipelineService:
    """Synchronous pipeline executor (legacy, replaced by Task #3)."""

    @staticmethod
    def execute_pipeline(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Execute a `source -> ai_matcher -> target` pipeline and return the result.

        Parameters
        ----------
        nodes : list of dict
            Pipeline node definitions. Each node must have an ``id`` and ``type``
            (one of ``source``, ``ai_matcher``, ``target``). ``source`` and
            ``target`` nodes must include ``config.connection_id``.
        edges : list of dict
            Pipeline edge definitions. Each edge must have ``source`` and
            ``target`` fields referencing node ids.

        Returns
        -------
        dict
            Result envelope (see module docstring).
        """
        logger.info("[pipeline] stage=validate nodes=%d edges=%d", len(nodes), len(edges))
        source_node, target_node, ai_matcher_node = PipelineService._validate_graph(nodes, edges)

        source_config = (source_node.get("config") or {})
        target_config = (target_node.get("config") or {})
        source_id = source_config.get("connection_id")
        target_id = target_config.get("connection_id")

        if not isinstance(source_id, int) or source_id <= 0:
            raise ValueError("Source node must have a connection_id configured")
        if not isinstance(target_id, int) or target_id <= 0:
            raise ValueError("Target node must have a connection_id configured")

        logger.info("[pipeline] stage=load_connections source_id=%d target_id=%d", source_id, target_id)
        source_conn, target_conn = PipelineService._load_connections(source_id, target_id)

        logger.info("[pipeline] stage=extract_schemas source='%s' target='%s'", source_conn.name, target_conn.name)
        source_schema = SchemaService.get_full_schema(source_conn)
        target_schema = SchemaService.get_full_schema(target_conn)
        logger.info("[pipeline] source_tables=%d target_tables=%d", len(source_schema), len(target_schema))

        on_the_fly_ddl: List[str] = []
        used_identity_matching = False
        if (target_conn.type or "").lower() == "sqlite" and not target_schema:
            if (source_conn.type or "").lower() != "sqlite":
                raise ValueError("On-the-fly target creation requires the source to be SQLite")
            logger.info("[pipeline] stage=create_target_on_the_fly tables=%d", len(source_schema))
            synthetic_target_schema, on_the_fly_ddl = PipelineService._create_target_on_the_fly(
                source_schema, target_conn
            )
            target_schema = synthetic_target_schema
            used_identity_matching = True

        if used_identity_matching:
            logger.info("[pipeline] stage=identity_matching")
            table_mappings, unmatched_source, unmatched_target = PipelineService._run_identity_matching(
                source_schema, target_schema
            )
        elif ai_matcher_node is not None:
            logger.info("[pipeline] stage=ai_matching")
            table_mappings, unmatched_source, unmatched_target = PipelineService._run_ai_matching(
                source_schema, target_schema
            )
        else:
            logger.info("[pipeline] stage=no_matching (no ai_matcher node)")
            table_mappings, unmatched_source, unmatched_target = [], [], list(target_schema.keys())

        logger.info(
            "[pipeline] stage=matching_complete matched=%d unmatched_source=%d unmatched_target=%d",
            len(table_mappings), len(unmatched_source), len(unmatched_target),
        )

        logger.info("[pipeline] stage=generate_migration_sql")
        mapping_rules = PipelineService._build_mapping_rules(table_mappings)
        migration_sql = SchemaMapperService.generate_migration_sql(
            mappings=mapping_rules,
            target_db_type="sqlite",
        )

        if on_the_fly_ddl:
            existing_ddl = list(migration_sql.get("ddl", []))
            existing_dml = list(migration_sql.get("dml", []))
            existing_warnings = list(migration_sql.get("warnings", []))
            migration_sql = {
                "ddl": list(on_the_fly_ddl) + existing_ddl,
                "dml": existing_dml,
                "warnings": existing_warnings,
                "total_statements": len(on_the_fly_ddl) + len(existing_ddl) + len(existing_dml),
            }

        rows_copied: Dict[str, int] = {}
        if on_the_fly_ddl:
            logger.info("[pipeline] stage=execute_migration ddl_statements=%d", len(on_the_fly_ddl))
            rows_copied = PipelineService._execute_target_migration(
                source_conn, target_conn, table_mappings, on_the_fly_ddl
            )
            logger.info("[pipeline] stage=migration_complete total_rows_copied=%d", sum(rows_copied.values()))

        logger.info("[pipeline] stage=done status=success")
        return {
            "status": "success",
            "source": source_conn.name,
            "target": target_conn.name,
            "source_connection_id": source_id,
            "target_connection_id": target_id,
            "table_mappings": table_mappings,
            "unmatched_source": unmatched_source,
            "unmatched_target": unmatched_target,
            "migration_sql": migration_sql,
            "rows_copied": rows_copied,
            "total_rows_copied": sum(rows_copied.values()),
        }

    # ── Graph validation ───────────────────────────────────────

    @staticmethod
    def _validate_graph(
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
    ):
        """Validate nodes + edges. Returns (source_node, target_node, ai_matcher_node)."""

        if not isinstance(nodes, list):
            raise ValueError("Pipeline nodes must be a list")
        if not isinstance(edges, list):
            raise ValueError("Pipeline edges must be a list")

        for node in nodes:
            node_type = node.get("type") if isinstance(node, dict) else None
            if node_type not in PIPELINE_NODE_TYPES:
                raise ValueError(f"Unknown node type: {node_type}")

        source_nodes = [n for n in nodes if n.get("type") == "source"]
        target_nodes = [n for n in nodes if n.get("type") == "target"]
        ai_matcher_nodes = [n for n in nodes if n.get("type") == "ai_matcher"]

        if len(source_nodes) == 0:
            raise ValueError("Pipeline must contain a source node")
        if len(source_nodes) > 1:
            raise ValueError("Pipeline must contain exactly one source node")
        if len(target_nodes) == 0:
            raise ValueError("Pipeline must contain a target node")
        if len(target_nodes) > 1:
            raise ValueError("Pipeline must contain exactly one target node")
        if len(ai_matcher_nodes) > 1:
            raise ValueError("Pipeline must contain at most one ai_matcher node")

        source_node = source_nodes[0]
        target_node = target_nodes[0]
        ai_matcher_node = ai_matcher_nodes[0] if ai_matcher_nodes else None

        # Validate connection_id presence and shape.
        source_config = source_node.get("config") or {}
        target_config = target_node.get("config") or {}

        source_conn_id = source_config.get("connection_id") if isinstance(source_config, dict) else None
        target_conn_id = target_config.get("connection_id") if isinstance(target_config, dict) else None

        if not isinstance(source_conn_id, int) or isinstance(source_conn_id, bool) or source_conn_id <= 0:
            raise ValueError("Source node must have a connection_id configured")
        if not isinstance(target_conn_id, int) or isinstance(target_conn_id, bool) or target_conn_id <= 0:
            raise ValueError("Target node must have a connection_id configured")

        # Build adjacency list from edges and validate reachability.
        adjacency = PipelineService._build_adjacency(edges, nodes)
        if not PipelineService._has_path(adjacency, source_node["id"], target_node["id"]):
            raise ValueError("Source and target are not connected in the pipeline graph")

        # If an ai_matcher is present, it must lie on the source → target path.
        if ai_matcher_node is not None:
            matcher_id = ai_matcher_node["id"]
            on_path = PipelineService._has_path(adjacency, source_node["id"], matcher_id) and \
                PipelineService._has_path(adjacency, matcher_id, target_node["id"])
            if not on_path:
                raise ValueError(
                    "AI matcher node must lie on the path between source and target"
                )

        return source_node, target_node, ai_matcher_node

    @staticmethod
    def _build_adjacency(
        edges: List[Dict[str, Any]],
        nodes: List[Dict[str, Any]],
    ) -> Dict[str, List[str]]:
        """Build a node-id → [neighbor-ids] adjacency list."""
        adjacency: Dict[str, List[str]] = {n["id"]: [] for n in nodes if isinstance(n, dict) and n.get("id")}
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            src = edge.get("source")
            tgt = edge.get("target")
            if src is None or tgt is None:
                continue
            adjacency.setdefault(src, []).append(tgt)
        return adjacency

    @staticmethod
    def _has_path(adjacency: Dict[str, List[str]], src: str, tgt: str) -> bool:
        """Breadth-first search: is there a directed path from ``src`` to ``tgt``?"""
        if src == tgt:
            return True
        visited = {src}
        queue = deque([src])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency.get(current, []):
                if neighbor == tgt:
                    return True
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return False

    # ── DB connections ─────────────────────────────────────────

    @staticmethod
    def _load_connections(source_id: int, target_id: int):
        """Load ``DBConnection`` rows for source and target. Raises ValueError if missing."""
        db = SessionLocal()
        try:
            source_conn = db.query(DBConnection).filter(DBConnection.id == source_id).first()
            if source_conn is None:
                raise ValueError(f"Connection {source_id} not found")

            target_conn = db.query(DBConnection).filter(DBConnection.id == target_id).first()
            if target_conn is None:
                raise ValueError(f"Connection {target_id} not found")

            # Detach from the session so callers can use the rows after `db.close()`.
            db.refresh(source_conn)
            db.refresh(target_conn)
            return source_conn, target_conn
        finally:
            db.close()

    # ── AI matching ────────────────────────────────────────────

    @staticmethod
    def _run_ai_matching(
        source_schema: Dict[str, List[Dict[str, Any]]],
        target_schema: Dict[str, List[Dict[str, Any]]],
    ):
        """For each source table, find the best target table via column-level confidence.

        Returns ``(table_mappings, unmatched_source, unmatched_target)``.
        """
        # Graceful handling: empty schema → empty result, no error.
        if not source_schema or not target_schema:
            return [], list(source_schema.keys()), list(target_schema.keys())

        matched_target_tables: set = set()
        table_mappings: List[Dict[str, Any]] = []
        unmatched_source: List[str] = []

        for src_table, src_cols in source_schema.items():
            best_target_table: Optional[str] = None
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

            if best_target_table is not None and best_confidence >= CONFIDENCE_THRESHOLD:
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

        return table_mappings, unmatched_source, unmatched_target

    # ── Mapping rules for SchemaMapperService ─────────────────

    @staticmethod
    def _build_mapping_rules(table_mappings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Translate AI-matched table mappings into SchemaMapperService rules."""
        rules: List[Dict[str, Any]] = []
        for mapping in table_mappings:
            details = mapping.get("details") or {}
            matches = details.get("matches", []) or []
            src_table = mapping.get("source_table")
            tgt_table = mapping.get("target_table")

            for match in matches:
                src_col = match.get("source")
                tgt_col = match.get("target")
                if not src_col or not tgt_col:
                    continue
                rules.append(
                    {
                        "source_column": src_col,
                        "source_table": src_table,
                        "target_column": tgt_col,
                        "target_table": tgt_table,
                        "transform": "direct",
                    }
                )

        return rules

    # ── On-the-fly target creation ──────────────────────────────

    @staticmethod
    def _create_target_on_the_fly(
        source_schema: Dict[str, List[Dict[str, Any]]],
        target_conn: DBConnection,
    ):
        """
        Build a synthetic identity target schema from ``source_schema`` and
        generate ``CREATE TABLE IF NOT EXISTS`` statements for each table.

        Returns ``(synthetic_target_schema, create_table_statements)``.
        The synthetic schema mirrors the source column-for-column (same
        names, same types) so downstream matching can use identity rules.
        """
        synthetic_target_schema: Dict[str, List[Dict[str, Any]]] = {}
        create_table_statements: List[str] = []

        for src_table, src_cols in source_schema.items():
            # Mirror the column list as-is.
            synthetic_target_schema[src_table] = list(src_cols)

            column_defs: List[str] = []
            for col in src_cols:
                col_name = col.get("name")
                if not col_name:
                    continue
                col_type = (col.get("type") or "TEXT").strip() or "TEXT"
                column_defs.append(f"{col_name} {col_type}")

            if column_defs:
                ddl = (
                    f"CREATE TABLE IF NOT EXISTS {src_table} (\n"
                    + ",\n".join(f"  {d}" for d in column_defs)
                    + "\n);"
                )
            else:
                ddl = f"CREATE TABLE IF NOT EXISTS {src_table} ();"
            create_table_statements.append(ddl)

        return synthetic_target_schema, create_table_statements

    # ── Target migration execution ──────────────────────────────

    @staticmethod
    def _execute_target_migration(
        source_conn: DBConnection,
        target_conn: DBConnection,
        table_mappings: List[Dict[str, Any]],
        create_table_statements: List[str],
    ) -> Dict[str, int]:
        """Execute CREATE TABLE statements and copy rows from source to target SQLite."""
        source_config = source_conn.config or {}
        target_config = target_conn.config or {}
        source_path = source_config.get("path")
        target_path = target_config.get("path")

        if not source_path or not target_path:
            raise ValueError("SQLite connection config must include 'path'")
        if not os.path.exists(source_path):
            raise ValueError(f"Source SQLite file not found: {source_path}")

        try:
            source_db = sqlite3.connect(source_path)
            source_db.row_factory = sqlite3.Row
            target_db = sqlite3.connect(target_path)

            source_cur = source_db.cursor()
            target_cur = target_db.cursor()

            # Create target tables
            for ddl in create_table_statements:
                target_cur.execute(ddl)

            rows_copied: Dict[str, int] = {}
            for mapping in table_mappings:
                src_table = mapping.get("source_table")
                tgt_table = mapping.get("target_table")
                if not src_table or not tgt_table:
                    continue

                source_cur.execute(f"SELECT * FROM {src_table}")
                rows = source_cur.fetchall()
                if not rows:
                    rows_copied[tgt_table] = 0
                    continue

                column_names = [desc[0] for desc in source_cur.description]
                col_list = ", ".join(column_names)
                placeholders = ", ".join(["?"] * len(column_names))

                target_cur.executemany(
                    f"INSERT INTO {tgt_table} ({col_list}) VALUES ({placeholders})",
                    [tuple(row) for row in rows]
                )
                rows_copied[tgt_table] = len(rows)

            target_db.commit()
            return rows_copied
        except Exception as e:
            raise ValueError(f"Failed to migrate data: {e}")
        finally:
            try:
                source_db.close()
            except Exception:
                pass
            try:
                target_db.close()
            except Exception:
                pass

    # ── Identity matching ───────────────────────────────────────

    @staticmethod
    def _run_identity_matching(
        source_schema: Dict[str, List[Dict[str, Any]]],
        target_schema: Dict[str, List[Dict[str, Any]]],
    ):
        """
        Identity matching: every source table that exists in the target
        schema matches itself with 100% confidence; every column maps to
        itself (direct transform).

        Returns ``(table_mappings, unmatched_source, unmatched_target)``
        with the same shape as ``_run_ai_matching`` so downstream
        consumers (``_build_mapping_rules``,
        ``SchemaMapperService.generate_migration_sql``) work unchanged.
        """
        if not source_schema or not target_schema:
            return [], list(source_schema.keys()), list(target_schema.keys())

        matched_targets: set = set()
        table_mappings: List[Dict[str, Any]] = []
        unmatched_source: List[str] = []

        for src_table, src_cols in source_schema.items():
            tgt_cols = target_schema.get(src_table)
            if tgt_cols is None:
                unmatched_source.append(src_table)
                continue

            tgt_col_names = {c.get("name") for c in tgt_cols if c.get("name")}
            matches: List[Dict[str, Any]] = []
            for col in src_cols:
                col_name = col.get("name")
                if not col_name:
                    continue
                if col_name in tgt_col_names:
                    matches.append(
                        {
                            "source": col_name,
                            "target": col_name,
                            "confidence": 100,
                            "reason": "Identity match (on-the-fly target)",
                        }
                    )

            table_mappings.append(
                {
                    "source_table": src_table,
                    "target_table": src_table,
                    "confidence": 100,
                    "details": {"matches": matches, "identity": True},
                }
            )
            matched_targets.add(src_table)

        unmatched_target = [
            t for t in target_schema.keys() if t not in matched_targets
        ]

        return table_mappings, unmatched_source, unmatched_target


# ── Module-level helpers (Task #1/#2 surface) ────────────────────────

def _resolve_published_version(db: Session, mapping_id: int) -> MappingVersion:
    """Return the current published version of a mapping.

    Pipelines pin a published version at create time so drift checks
    (Task #2) have a stable baseline. Raises 422 if the mapping has no
    current published version yet (i.e. still draft).
    """
    m = db.query(Mapping).filter(Mapping.id == mapping_id).first()
    if not m:
        raise HTTPException(status_code=404, detail=f"mapping {mapping_id} not found")
    if m.status != "published" or m.current_version_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"mapping {mapping_id} is not published; publish it before "
                f"creating a pipeline (pinned mapping_version_id required)"
            ),
        )
    v = (
        db.query(MappingVersion)
        .filter(MappingVersion.id == m.current_version_id)
        .first()
    )
    if not v:
        raise HTTPException(
            status_code=422,
            detail=f"mapping {mapping_id} has no readable current version",
        )
    return v


# ── Task #1: CRUD surface on the persistent Pipeline / PipelineRun ─

class PipelineCRUD:
    """Static-method facade for CRUD on Pipeline / PipelineRun.

    Split from `PipelineService.execute_pipeline` so Task #3 can replace
    the executor without touching this surface. Imports stay shared at
    the module level.
    """

    @staticmethod
    def create_pipeline(
        db: Session,
        *,
        name: str,
        source_connection_id: int,
        target_connection_id: int,
        mapping_id: int,
        actor: str,
        execution_mode: str = "manual",
        load_strategy: Optional[str] = None,
    ) -> Pipeline:
        if source_connection_id == target_connection_id:
            raise HTTPException(
                status_code=422,
                detail="source_connection_id and target_connection_id must differ",
            )
        for cid, label in ((source_connection_id, "source"), (target_connection_id, "target")):
            if not db.query(DBConnection).filter(
                DBConnection.id == cid,
                DBConnection.is_deleted == False,  # noqa: E712
            ).first():
                raise HTTPException(
                    status_code=404, detail=f"{label} connection {cid} not found",
                )

        # Pin the mapping's current published version (FR1). drift
        # checks (Task #2) will compare against this snapshot.
        version = _resolve_published_version(db, mapping_id)

        # Bug #14: the pipeline inherits its connections from the published
        # mapping's contract — a mapping published against other connections
        # would make the drift baseline (and Task #3's execution) meaningless.
        mapping = db.query(Mapping).filter(Mapping.id == mapping_id).first()
        if mapping.source_id is None or mapping.target_id is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"mapping {mapping_id}'s original connections no longer exist; "
                    "re-publish it against current connections before creating a pipeline"
                ),
            )
        if mapping.source_id != source_connection_id or mapping.target_id != target_connection_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"mapping {mapping_id} was published against connections "
                    f"{mapping.source_id}→{mapping.target_id}, not "
                    f"{source_connection_id}→{target_connection_id}; a pipeline must use "
                    "the connections its mapping was published against"
                ),
            )

        pipeline = Pipeline(
            name=name,
            source_connection_id=source_connection_id,
            target_connection_id=target_connection_id,
            mapping_id=mapping_id,
            mapping_version_id=version.id,
            enabled=True,
            execution_mode=execution_mode,
            load_strategy=load_strategy,
            created_by=actor,
        )
        db.add(pipeline)
        db.flush()
        # Task #5: every pipeline gets a default retry policy at create time
        # so run_pipeline_task always has one to read (3 attempts, 60s backoff).
        db.add(RetryPolicy(pipeline_id=pipeline.id, max_attempts=3, backoff_seconds=60))
        record_audit(
            db, "pipeline_created", actor=actor,
            connection_id=source_connection_id,
            payload={
                "pipeline_id": pipeline.id,
                "name": name,
                "source_connection_id": source_connection_id,
                "target_connection_id": target_connection_id,
                "mapping_id": mapping_id,
                "mapping_version_id": version.id,
                "execution_mode": execution_mode,
                "load_strategy": load_strategy,
            },
        )
        db.commit()
        db.refresh(pipeline)
        return pipeline

    @staticmethod
    def get_pipeline(db: Session, pipeline_id: int) -> Pipeline:
        p = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="pipeline not found")
        return p

    @staticmethod
    def list_pipelines(
        db: Session, *, limit: int = 50, offset: int = 0,
    ) -> tuple:
        """Return (items, total) for paginated list (mirrors the
        MappingService.list_pagination helper)."""
        base = db.query(Pipeline)
        total = base.count()
        items = base.order_by(Pipeline.created_at.desc()).offset(offset).limit(limit).all()
        return items, total

    @staticmethod
    def update_pipeline(
        db: Session,
        pipeline_id: int,
        *,
        name: Optional[str],
        enabled: Optional[bool],
        actor: str,
    ) -> Pipeline:
        p = PipelineCRUD.get_pipeline(db, pipeline_id)
        before = {"name": p.name, "enabled": bool(p.enabled)}
        if name is not None:
            p.name = name
        if enabled is not None:
            p.enabled = enabled
        db.flush()
        record_audit(
            db, "pipeline_updated", actor=actor,
            connection_id=p.source_connection_id,
            payload={
                "pipeline_id": p.id,
                "before": before,
                "after": {"name": p.name, "enabled": bool(p.enabled)},
            },
        )
        db.commit()
        db.refresh(p)
        return p

    @staticmethod
    def delete_pipeline(db: Session, pipeline_id: int, *, actor: str) -> None:
        p = PipelineCRUD.get_pipeline(db, pipeline_id)
        before = {"name": p.name, "mapping_id": p.mapping_id}
        # Hard delete with cascade on schedules, retry_policy, runs,
        # run_steps (declared in app/models/pipeline.py). Suitable for
        # a draft that hasn't been published; for already-published
        # pipelines we may want a soft-delete (FR DoD) -- flagged as a
        # follow-up.
        db.delete(p)
        db.flush()
        record_audit(
            db, "pipeline_deleted", actor=actor,
            connection_id=p.source_connection_id,
            payload={"pipeline_id": pipeline_id, "before": before},
        )
        db.commit()

    @staticmethod
    def list_runs(
        db: Session,
        pipeline_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        trigger: Optional[str] = None,
    ) -> tuple:
        # Verify the pipeline exists so 404 fires cleanly instead of an
        # empty list (which would be ambiguous).
        PipelineCRUD.get_pipeline(db, pipeline_id)
        base = db.query(PipelineRun).filter(PipelineRun.pipeline_id == pipeline_id)
        if status:
            base = base.filter(PipelineRun.status == status)
        if trigger:
            base = base.filter(PipelineRun.trigger == trigger)
        total = base.count()
        items = base.order_by(PipelineRun.id.desc()).offset(offset).limit(limit).all()
        return items, total

    @staticmethod
    def get_run(db: Session, pipeline_id: int, run_id: int) -> PipelineRun:
        PipelineCRUD.get_pipeline(db, pipeline_id)
        run = db.query(PipelineRun).filter(
            PipelineRun.id == run_id, PipelineRun.pipeline_id == pipeline_id,
        ).first()
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    # ── Task #3/#9: manual run + concurrency guard ────────────────────

    @staticmethod
    def create_run(
        db: Session,
        pipeline_id: int,
        *,
        trigger: str,
        actor: str,
        parent_run_id: Optional[int] = None,
    ) -> PipelineRun:
        """Create a new PipelineRun row, enforcing at most one active run
        per pipeline (Task #9). ``SELECT ... FOR UPDATE`` serializes
        concurrent callers on Postgres; SQLite (used in tests/dev) has no
        row-level locking, so the check-then-insert has a small race
        window there — acceptable for a dev-only gap, not for the
        Postgres-backed production path this guard actually protects."""
        p = (
            db.query(Pipeline)
            .filter(Pipeline.id == pipeline_id)
            .with_for_update()
            .first()
        )
        if not p:
            raise HTTPException(status_code=404, detail="pipeline not found")
        if not p.enabled:
            raise HTTPException(status_code=422, detail="pipeline is disabled")

        active = db.query(PipelineRun).filter(
            PipelineRun.pipeline_id == pipeline_id,
            PipelineRun.status.in_(ACTIVE_RUN_STATUSES),
        ).first()
        if active:
            raise HTTPException(
                status_code=409,
                detail=f"pipeline {pipeline_id} already has an active run ({active.id})",
            )

        run = PipelineRun(
            pipeline_id=pipeline_id, status="pending", trigger=trigger,
            parent_run_id=parent_run_id,
        )
        db.add(run)
        db.flush()

        record_audit(
            db, "pipeline_run_started" if trigger != "rerun" else "pipeline_rerun",
            actor=actor, connection_id=p.source_connection_id,
            payload={
                "pipeline_id": pipeline_id, "run_id": run.id, "trigger": trigger,
                **({"original_run_id": parent_run_id} if parent_run_id else {}),
            },
        )
        db.commit()
        db.refresh(run)
        return run

    # ── Task #4: schedule CRUD ─────────────────────────────────────────

    @staticmethod
    def upsert_schedule(
        db: Session, pipeline_id: int, *,
        cron_expression: str, enabled: bool, timezone: str, actor: str,
    ) -> Schedule:
        from croniter import croniter
        p = PipelineCRUD.get_pipeline(db, pipeline_id)
        if not croniter.is_valid(cron_expression):
            raise HTTPException(status_code=422, detail=f"invalid cron expression: {cron_expression}")

        schedule = db.query(Schedule).filter(Schedule.pipeline_id == pipeline_id).first()
        if schedule:
            schedule.cron_expression = cron_expression
            schedule.enabled = enabled
            schedule.timezone = timezone
        else:
            schedule = Schedule(
                pipeline_id=pipeline_id, cron_expression=cron_expression,
                enabled=enabled, timezone=timezone,
            )
            db.add(schedule)
        db.flush()
        record_audit(
            db, "pipeline_schedule_updated", actor=actor,
            connection_id=p.source_connection_id,
            payload={"pipeline_id": pipeline_id, "cron_expression": cron_expression, "enabled": enabled},
        )
        db.commit()
        db.refresh(schedule)
        return schedule

    @staticmethod
    def delete_schedule(db: Session, pipeline_id: int, *, actor: str) -> None:
        p = PipelineCRUD.get_pipeline(db, pipeline_id)
        schedule = db.query(Schedule).filter(Schedule.pipeline_id == pipeline_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="schedule not found")
        db.delete(schedule)
        record_audit(
            db, "pipeline_schedule_deleted", actor=actor,
            connection_id=p.source_connection_id,
            payload={"pipeline_id": pipeline_id},
        )
        db.commit()

    @staticmethod
    def toggle_schedule(db: Session, pipeline_id: int, *, enabled: bool, actor: str) -> Schedule:
        p = PipelineCRUD.get_pipeline(db, pipeline_id)
        schedule = db.query(Schedule).filter(Schedule.pipeline_id == pipeline_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="schedule not found")
        schedule.enabled = enabled
        record_audit(
            db, "pipeline_schedule_toggled", actor=actor,
            connection_id=p.source_connection_id,
            payload={"pipeline_id": pipeline_id, "enabled": enabled},
        )
        db.commit()
        db.refresh(schedule)
        return schedule

    # ── Task #5: retry policy CRUD ─────────────────────────────────────

    @staticmethod
    def upsert_retry_policy(
        db: Session, pipeline_id: int, *,
        max_attempts: int, backoff_seconds: int,
        retryable_error_patterns: Optional[List[str]], actor: str,
    ) -> RetryPolicy:
        p = PipelineCRUD.get_pipeline(db, pipeline_id)
        policy = db.query(RetryPolicy).filter(RetryPolicy.pipeline_id == pipeline_id).first()
        if policy:
            policy.max_attempts = max_attempts
            policy.backoff_seconds = backoff_seconds
            policy.retryable_error_patterns = retryable_error_patterns
        else:
            policy = RetryPolicy(
                pipeline_id=pipeline_id, max_attempts=max_attempts,
                backoff_seconds=backoff_seconds,
                retryable_error_patterns=retryable_error_patterns,
            )
            db.add(policy)
        db.flush()
        record_audit(
            db, "pipeline_retry_policy_updated", actor=actor,
            connection_id=p.source_connection_id,
            payload={"pipeline_id": pipeline_id, "max_attempts": max_attempts},
        )
        db.commit()
        db.refresh(policy)
        return policy

    # ── Task #2: Drift validation (FR2 / AC2) ────────────────────

    @staticmethod
    def validate_drift(db: Session, pipeline_id: int, *, actor: str = "system"):
        """Compare the live source schema to the snapshot captured when the
        pinned mapping_version was published.

        Returns a DriftValidationRead-shaped dict. `has_drift=True` means
        the run must be blocked (AC2). Task #3's executor will call this
        before extract and record a PipelineRun with status='failed' and
        error_message naming the drift when blocked. A pinned version with
        no source snapshot is a 422 (Bug #17) — fail closed, the executor
        must block that run too.

        Implementation note (review flag): the task spec recommends
        eventually hashing only the subset of source columns actually
        referenced by the mapping's FieldMapping.sources so unrelated
        source-table changes don't spuriously block unrelated pipelines.
        For Task #2 we hash the full source schema; the per-mapping
        subset is a follow-up if false-positive blocks become a problem
        in practice.
        """
        p = PipelineCRUD.get_pipeline(db, pipeline_id)
        version = (
            db.query(MappingVersion)
            .filter(MappingVersion.id == p.mapping_version_id)
            .first()
        )
        if version is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"pipeline {p.id} pins mapping_version {p.mapping_version_id} "
                    "which no longer exists"
                ),
            )

        snapshot = (version.schema_snapshot or {}).get("source") or {}
        if not snapshot:
            # Bug #17: no baseline means the check cannot pass — fail closed.
            raise HTTPException(
                status_code=422,
                detail=(
                    f"pinned mapping version {version.id} has no source schema "
                    "snapshot; re-publish the mapping to establish a drift baseline"
                ),
            )

        # Fetch the live source schema.
        source_conn = (
            db.query(DBConnection)
            .filter(DBConnection.id == p.source_connection_id)
            .first()
        )
        if source_conn is None:
            raise HTTPException(
                status_code=422,
                detail=f"source connection {p.source_connection_id} no longer exists",
            )
        try:
            live_source = SchemaService.get_full_schema(source_conn)
        except Exception as exc:
            logger.warning(
                "validate_drift: schema fetch failed for pipeline %s: %s",
                pipeline_id, exc,
            )
            raise HTTPException(
                status_code=502,
                detail=f"failed to fetch live source schema: {exc}",
            ) from exc

        # Bugs #15/#16: drift is defined by the normalized hashes, so
        # has_drift == (baseline_hash != current_hash) always holds and
        # column ordering never produces a false positive.
        live_hash = compute_schema_hash(live_source)
        baseline_hash = compute_schema_hash(snapshot)
        has_drift = live_hash != baseline_hash
        changed_tables = _diff_tables(snapshot, live_source)

        result = {
            "pipeline_id": p.id,
            "has_drift": has_drift,
            "baseline_hash": baseline_hash,
            "current_hash": live_hash,
            "changed_tables": changed_tables,
            "message": (
                "source schema has changed since mapping was published; "
                "re-publish the mapping or update it before running"
            ) if has_drift else "no drift detected",
        }
        # Record an audit event so the drift check itself is traceable.
        # (Distinct from the run-failure audit that Task #3's executor
        # will emit when it blocks a run because of this result.)
        record_audit(
            db, "pipeline_drift_check", actor=actor,
            connection_id=p.source_connection_id,
            payload={
                "pipeline_id": p.id,
                "mapping_version_id": version.id,
                "has_drift": has_drift,
                "changed_tables": changed_tables,
            },
            status="failure" if has_drift else "success",
        )
        db.commit()
        return result


def _diff_tables(baseline: Dict[str, Any], live: Dict[str, Any]) -> List[str]:
    """Names of tables that drifted: present in only one side, or present
    in both with different (normalized) column definitions (Bug #18).
    Column order is normalized away, so a reordered column list is not a
    change. Surfaced in DriftValidationRead so drift reports name the
    affected tables, not just "something changed"."""
    changed = set(baseline.keys()) ^ set(live.keys())
    for table in set(baseline.keys()) & set(live.keys()):
        if _normalize_schema(baseline[table]) != _normalize_schema(live[table]):
            changed.add(table)
    return sorted(changed)
