"""Risk & Compliance Center aggregation (Enterprise v2, E05).

This service does not detect anything new — it normalizes signals other
services already compute into a single ``RiskFinding`` shape:

- Missing target tables / type mismatches — DiffService, scoped to
  connection pairs that have a real (non-deleted) Mapping between them
  (mirrors the Schema Topology graph's own scope in schema.py).
- Schema drift — the latest DriftEvent per connection.
- PII exposure — persisted ColumnClassification rows (PII/Sensitive).
- Unsupported transformations — MappingValidationService issues on
  non-deleted mappings.
- Broken dependencies — PipelineRun failures whose error indicates a
  schema-drift block (the same signal pipeline_executor already uses to
  fire the ``pipeline:drift_impact`` notification).

Each connection-pair diff requires a live connector round-trip; a single
unreachable connection must not blank the whole register, so every
adapter is isolated the same way DashboardService isolates its modules
(catch, log, continue — `partial=True` surfaces which adapter failed).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.models.connection import DBConnection
from app.models.drift_event import DriftEvent
from app.models.mapping import FieldMapping, Mapping
from app.models.pipeline import Pipeline, PipelineRun
from app.models.schema_catalog import CatalogColumn, CatalogTable, ColumnClassification
from app.services.diff_service import DiffService
from app.services.mapping_validation_service import MappingValidationService
from app.services import risk_cache
from app.services.schema_service import SchemaService

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _finding_id(category: str, *parts: Any) -> str:
    raw = "|".join([category, *(str(p) for p in parts)])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class RiskAggregationService:
    def __init__(self, db: Session):
        self.db = db

    def get_register(
        self,
        category: str | None = None,
        severity: str | None = None,
        connection_id: int | None = None,
    ) -> dict[str, Any]:
        cache_key = f"risk_register:{id(self.db.get_bind())}:all"
        cached = risk_cache.get(cache_key)
        if cached is not None:
            findings = list(cached["findings"])
            partial = cached["partial"]
            partial_reasons = list(cached["partial_reasons"])
        else:
            findings = []
            partial = False
            partial_reasons = []
            adapters = [
                ("pii_exposure", self._pii_findings),
                ("schema_drift", self._drift_findings),
                ("unsupported_transformation", self._validation_findings),
                ("broken_dependency", self._drift_impact_findings),
                ("missing_target_table", self._mapping_diff_findings),
            ]
            for name, adapter in adapters:
                try:
                    findings.extend(adapter())
                except Exception as exc:  # noqa: BLE001 — isolate one signal source from the rest
                    logger.warning("[risk] adapter=%s failed: %s", name, exc, exc_info=True)
                    partial = True
                    partial_reasons.append(name)
            risk_cache.set(cache_key, {
                "findings": list(findings),
                "partial": partial,
                "partial_reasons": list(partial_reasons),
            })

        if category:
            findings = [f for f in findings if f["category"] == category]
        if severity:
            findings = [f for f in findings if f["severity"] == severity]
        if connection_id is not None:
            findings = [f for f in findings if f.get("connection_id") == connection_id]

        findings.sort(key=lambda f: (_SEVERITY_RANK.get(f["severity"], 9), f["category"]))

        by_category: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        for f in findings:
            by_category[f["category"]] = by_category.get(f["category"], 0) + 1
            by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

        result = {
            "total": len(findings),
            "findings": findings,
            "facets": {"by_category": by_category, "by_severity": by_severity},
            "partial": partial,
            "partial_reason": ", ".join(partial_reasons) if partial_reasons else None,
        }
        return result

    # ── PII exposure ────────────────────────────────────────────────
    def _pii_findings(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(ColumnClassification, CatalogColumn, CatalogTable, DBConnection)
            .join(CatalogColumn, ColumnClassification.column_id == CatalogColumn.id)
            .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
            .join(DBConnection, CatalogTable.connection_id == DBConnection.id)
            .filter(ColumnClassification.label.in_(["PII", "Sensitive"]))
            .filter(DBConnection.is_deleted.is_(False))
            .all()
        )
        out = []
        for cls, col, table, conn in rows:
            if cls.label == "PII" and cls.level == "High":
                severity = "critical"
            elif cls.label == "PII" or cls.level == "High":
                severity = "high"
            elif cls.level == "Medium":
                severity = "medium"
            else:
                severity = "low"
            out.append({
                "id": _finding_id("pii_exposure", col.id),
                "category": "pii_exposure",
                "severity": severity,
                "title": f"{cls.label} data in {table.table_name}.{col.column_name}",
                "description": (
                    f"Classified as {cls.label} ({cls.level} sensitivity, "
                    f"{cls.confidence:.0%} confidence, via {cls.method})."
                ),
                "root_cause": f"Column matched a {cls.method.replace('_', ' ')} PII/sensitivity signal.",
                "impact": "Exposed to any role with read access to this connection unless masked.",
                "remediation": "Confirm classification, apply a masking policy, or restrict role access.",
                "effort": "S",
                "connection_id": conn.id,
                "connection_name": conn.name,
                "table": table.table_name,
                "column": col.column_name,
                "detected_at": cls.classified_at,
                "evidence": {"label": cls.label, "level": cls.level, "confidence": cls.confidence, "method": cls.method},
            })
        return out

    # ── Schema drift ────────────────────────────────────────────────
    def _drift_findings(self) -> list[dict[str, Any]]:
        conns = {c.id: c for c in self.db.query(DBConnection).filter(DBConnection.is_deleted.is_(False)).all()}
        out = []
        for conn_id, conn in conns.items():
            latest = (
                self.db.query(DriftEvent)
                .filter(DriftEvent.connection_id == conn_id)
                .order_by(DriftEvent.detected_at.desc())
                .first()
            )
            if latest is None:
                continue
            for name in (latest.tables_removed or []):
                out.append(self._drift_finding(conn, "high", f"Table removed: {name}", name, None, latest))
            for entry in (latest.type_changes or []):
                if not isinstance(entry, dict):
                    continue
                out.append(self._drift_finding(
                    conn, "medium",
                    f"Type change: {entry.get('table')}.{entry.get('column')} "
                    f"({entry.get('old_type')} → {entry.get('new_type')})",
                    entry.get("table"), entry.get("column"), latest,
                ))
            for entry in (latest.columns_removed or []):
                if not isinstance(entry, dict):
                    continue
                out.append(self._drift_finding(
                    conn, "medium", f"Column removed: {entry.get('table')}.{entry.get('column')}",
                    entry.get("table"), entry.get("column"), latest,
                ))
        return out

    def _drift_finding(self, conn, severity, title, table, column, event: DriftEvent) -> dict[str, Any]:
        return {
            "id": _finding_id("schema_drift", conn.id, table, column, event.id),
            "category": "schema_drift",
            "severity": severity,
            "title": f"{conn.name}: {title}",
            "description": f"Detected in drift scan #{event.id} on {conn.name}.",
            "root_cause": "The connected schema changed since the last scan.",
            "impact": "Dependent mappings, pipelines, or queries referencing this table/column may break.",
            "remediation": "Review the drift history and update affected mappings or pipelines.",
            "effort": "M",
            "connection_id": conn.id,
            "connection_name": conn.name,
            "table": table,
            "column": column,
            "detected_at": event.detected_at,
            "evidence": {"drift_event_id": event.id},
        }

    # ── Unsupported transformation ──────────────────────────────────
    def _validation_findings(self) -> list[dict[str, Any]]:
        mappings = (
            self.db.query(Mapping)
            .options(selectinload(Mapping.edges))
            .filter(Mapping.deleted_at.is_(None))
            .all()
        )
        conn_ids = {m.source_id for m in mappings if m.source_id} | {m.target_id for m in mappings if m.target_id}
        conns = {c.id: c for c in self.db.query(DBConnection).filter(DBConnection.id.in_(conn_ids)).all()}
        out = []
        for mapping in mappings:
            result = MappingValidationService.validate_mapping(mapping)
            for issue in result["issues"]:
                if issue["verdict"] == "ok":
                    continue
                severity = "high" if issue["verdict"] == "blocking" else "medium"
                src_conn = conns.get(mapping.source_id)
                out.append({
                    "id": _finding_id("unsupported_transformation", mapping.id, issue.get("edge_id")),
                    "category": "unsupported_transformation",
                    "severity": severity,
                    "title": f"{mapping.name}: {issue['message']}",
                    "description": issue["message"],
                    "root_cause": "The mapping's field-level transformation cannot safely bridge these types.",
                    "impact": "Publishing or executing this mapping may fail or silently lose data.",
                    "remediation": "Add a compatible transformation (e.g. CAST) or adjust the target column type.",
                    "effort": "S",
                    "connection_id": src_conn.id if src_conn else None,
                    "connection_name": src_conn.name if src_conn else None,
                    "mapping_id": mapping.id,
                    "table": None,
                    "column": None,
                    "detected_at": mapping.updated_at,
                    "evidence": {"edge_id": issue.get("edge_id"), "verdict": issue["verdict"]},
                })
        return out

    # ── Broken dependency (pipeline blocked by drift) ───────────────
    def _drift_impact_findings(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(PipelineRun, Pipeline)
            .join(Pipeline, PipelineRun.pipeline_id == Pipeline.id)
            .filter(PipelineRun.status == "failed")
            .filter(PipelineRun.error_message.ilike("%blocked by schema drift%"))
            .order_by(PipelineRun.started_at.desc())
            .limit(200)
            .all()
        )
        conn_ids = {p.source_connection_id for _, p in rows}
        conns = {c.id: c for c in self.db.query(DBConnection).filter(DBConnection.id.in_(conn_ids)).all()}
        out = []
        for run, pipeline in rows:
            conn = conns.get(pipeline.source_connection_id)
            out.append({
                "id": _finding_id("broken_dependency", pipeline.id, run.id),
                "category": "broken_dependency",
                "severity": "high",
                "title": f"Pipeline '{pipeline.name}' blocked by schema drift",
                "description": (run.error_message or "")[:300],
                "root_cause": "The pipeline's source schema drifted since its mapping was pinned.",
                "impact": "This pipeline cannot run until the drift is resolved or the mapping is updated.",
                "remediation": "Review the drift, then re-pin or republish the pipeline's mapping.",
                "effort": "M",
                "connection_id": conn.id if conn else None,
                "connection_name": conn.name if conn else None,
                "mapping_id": pipeline.mapping_id,
                "table": None,
                "column": None,
                "detected_at": run.started_at,
                "evidence": {"pipeline_id": pipeline.id, "run_id": run.id},
            })
        return out

    def _real_mapped_sources(self, source_id: int, target_id: int) -> set[str]:
        """Source tables with a real, currently-published correspondence to
        *target_id* — mirrors schema.py's `_get_real_table_mappings` (kept
        as a duplicate, not an import: services should not depend on
        routers). ``Mapping.edges`` cannot be used directly here — it
        returns every historical version's snapshot rows since
        ``FieldMapping.mapping_id`` has no version filter, so it would
        falsely "remember" tables from versions that are no longer live.
        """
        mapping = (
            self.db.query(Mapping)
            .filter(
                Mapping.source_id == source_id,
                Mapping.target_id == target_id,
                Mapping.status == "published",
                Mapping.deleted_at.is_(None),
            )
            .order_by(Mapping.updated_at.desc())
            .first()
        )
        if mapping is None or mapping.current_version_id is None:
            return set()
        edges = (
            self.db.query(FieldMapping)
            .filter(FieldMapping.version_id == mapping.current_version_id)
            .all()
        )
        return {
            edge.sources[0]["table"]
            for edge in edges
            if edge.sources and edge.sources[0].get("table")
        }

    # ── Missing target table / type mismatch ────────────────────────
    def _mapping_diff_findings(self) -> list[dict[str, Any]]:
        mappings = (
            self.db.query(Mapping)
            .filter(Mapping.deleted_at.is_(None))
            .filter(Mapping.source_id.isnot(None))
            .filter(Mapping.target_id.isnot(None))
            .all()
        )
        # Dedupe by (source_id, target_id) — multiple mappings can share a
        # connection pair; diffing the same live pair twice wastes a
        # connector round-trip for no new information.
        pairs = {(m.source_id, m.target_id) for m in mappings}
        conn_ids = {cid for pair in pairs for cid in pair}
        conns = {c.id: c for c in self.db.query(DBConnection).filter(DBConnection.id.in_(conn_ids)).all()}

        out = []
        for source_id, target_id in pairs:
            source_conn = conns.get(source_id)
            target_conn = conns.get(target_id)
            if not source_conn or not target_conn:
                continue
            try:
                source_schema = SchemaService.get_full_schema(source_conn)
                target_schema = SchemaService.get_full_schema(target_conn)
            except Exception as exc:  # noqa: BLE001 — one unreachable connection shouldn't block the register
                logger.warning(
                    "[risk] mapping_diff source=%s target=%s unreachable: %s",
                    source_id, target_id, exc,
                )
                continue

            diff = DiffService.compare_schemas(source_schema, target_schema)
            real_mapped_sources = self._real_mapped_sources(source_id, target_id)
            for table in diff["missing_tables_in_target"]:
                if table in real_mapped_sources:
                    continue
                out.append({
                    "id": _finding_id("missing_target_table", source_id, target_id, table),
                    "category": "missing_target_table",
                    "severity": "high",
                    "title": f"'{table}' not found in {target_conn.name}",
                    "description": f"Source table '{table}' in {source_conn.name} has no matching table in {target_conn.name}.",
                    "root_cause": "No table of the same name exists in the target, and no published mapping renames it.",
                    "impact": "Any pipeline or mapping expecting this table in the target will fail.",
                    "remediation": f"Create the target table or add a mapping that maps '{table}' to its target equivalent.",
                    "effort": "M",
                    "connection_id": source_conn.id,
                    "connection_name": source_conn.name,
                    "table": table,
                    "column": None,
                    "detected_at": None,
                    "evidence": {"source_id": source_id, "target_id": target_id},
                })
            for table, table_diff in diff["table_diffs"].items():
                for mismatch in table_diff.get("type_mismatches", []):
                    out.append({
                        "id": _finding_id("missing_target_table", "type", source_id, target_id, table, mismatch["column"]),
                        "category": "missing_target_table",
                        "severity": "medium",
                        "title": f"Type mismatch: {table}.{mismatch['column']}",
                        "description": (
                            f"{source_conn.name}.{table}.{mismatch['column']} is "
                            f"{mismatch['source_type']}, {target_conn.name} has "
                            f"{mismatch['target_type']}."
                        ),
                        "root_cause": "The source and target columns of the same name have diverged in type.",
                        "impact": "Direct (unmapped) writes between these tables may fail or truncate data.",
                        "remediation": "Reconcile the column type or add an explicit cast in a mapping.",
                        "effort": "S",
                        "connection_id": source_conn.id,
                        "connection_name": source_conn.name,
                        "table": table,
                        "column": mismatch["column"],
                        "detected_at": None,
                        "evidence": {"source_type": mismatch["source_type"], "target_type": mismatch["target_type"]},
                    })
        return out
