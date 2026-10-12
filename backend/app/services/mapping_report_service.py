"""Mapping Documentation & Migration Reports (Enterprise v2, E15).

Composes existing services into portable Markdown artifacts — this
module generates nothing new about the mapping itself; every fact here
is read from a real, already-existing source (mapping_service,
mapping_validation_service, schema_comparison_service, risk_service).
A section whose upstream data isn't available renders an explicit
"not available" note, never a fabricated value (per the no-mock-UI
rule) — e.g. an unreachable connector degrades the schema-diff section
without failing the whole report.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.connection import DBConnection
from app.models.mapping import Mapping
from app.services.mapping_service import MappingService
from app.services.mapping_validation_service import MappingValidationService

logger = logging.getLogger(__name__)


def _edge_row(edge: Any) -> str:
    sources = ", ".join(f"{s.get('table')}.{s.get('column')}" for s in (edge.sources or []))
    kind = (edge.transformation or {}).get("kind", "direct")
    confidence = f"{edge.ai_confidence:.0%}" if edge.ai_confidence is not None else "—"
    return f"| {sources} | {edge.target_table}.{edge.target_column} | {kind} | {edge.origin} | {confidence} |"


class MappingDocumentationService:
    @staticmethod
    def generate(db: Session, mapping_id: int) -> dict[str, str]:
        mapping: Mapping = MappingService.get_mapping(db, mapping_id)
        source = db.query(DBConnection).filter(DBConnection.id == mapping.source_id).first()
        target = db.query(DBConnection).filter(DBConnection.id == mapping.target_id).first()
        # build-validation B-E15-07 investigated: mapping.edges lazy-loads
        # once here and MappingValidationService.validate_mapping (below)
        # reuses the same already-populated SQLAlchemy relationship on
        # this instance — one query for the mapping, one for its edges,
        # regardless of edge count. That's normal single-request cost, not
        # N+1; there's no batch/multi-mapping report endpoint in this
        # codebase for it to multiply across.
        edges = [e for e in (mapping.edges or []) if e.version_id is None]

        lines = [
            f"# Mapping Documentation — {mapping.name}",
            "",
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Overview",
            "",
            f"- **Source:** {source.name if source else 'unknown'}",
            f"- **Target:** {target.name if target else 'unknown'}",
            f"- **Status:** {mapping.status}",
            f"- **Review stage:** {mapping.review_stage}",
            f"- **Created by:** {mapping.created_by}",
            f"- **Field mappings:** {len(edges)}",
            "",
            "## Field Mappings",
            "",
            "| Source | Target | Transformation | Origin | AI Confidence |",
            "|---|---|---|---|---|",
        ]
        if edges:
            lines.extend(_edge_row(e) for e in edges)
        else:
            lines.append("| _no field mappings defined yet_ | | | | |")

        validation = MappingValidationService.validate_mapping(mapping)
        lines.extend([
            "",
            "## Validation Summary",
            "",
            f"- OK: {validation['ok_count']}",
            f"- Warnings: {validation['warning_count']}",
            f"- Blocking issues: {validation['blocking_count']}",
        ])
        if validation["issues"]:
            lines.append("")
            for issue in validation["issues"]:
                if issue["verdict"] != "ok":
                    lines.append(f"- **{issue['verdict']}** (edge #{issue['edge_id']}): {issue['message']}")

        logger.info("[mapping_report] stage=documentation_generated mapping_id=%s", mapping_id)
        content = "\n".join(lines) + "\n"
        return {"content": content, "filename": f"mapping-{mapping_id}-documentation.md"}


class MigrationReportService:
    @staticmethod
    def generate(db: Session, mapping_id: int) -> dict[str, str]:
        mapping: Mapping = MappingService.get_mapping(db, mapping_id)
        source = db.query(DBConnection).filter(DBConnection.id == mapping.source_id).first()
        target = db.query(DBConnection).filter(DBConnection.id == mapping.target_id).first()

        lines = [
            f"# Migration Readiness Report — {mapping.name}",
            "",
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            f"- **Source:** {source.name if source else 'unknown'}",
            f"- **Target:** {target.name if target else 'unknown'}",
            f"- **Review stage:** {mapping.review_stage}",
            "",
            "## Validation",
            "",
        ]
        validation = MappingValidationService.validate_mapping(mapping)
        lines.append(
            f"{validation['ok_count']} ok, {validation['warning_count']} warning(s), "
            f"{validation['blocking_count']} blocking issue(s)."
        )

        lines.extend(["", "## Schema Comparison", ""])
        if source is None or target is None:
            lines.append("_Not available — source or target connection is missing._")
        else:
            try:
                from app.services.schema_comparison_service import SchemaComparisonService
                from app.services.schema_service import SchemaService

                comparison = SchemaComparisonService.compare(
                    SchemaService.get_full_schema(source), SchemaService.get_full_schema(target),
                )
                summary = comparison["summary"]
                lines.append(
                    f"{summary['matched_tables']} matched table(s), "
                    f"{summary['source_only_tables']} source-only, "
                    f"{summary['target_only_tables']} target-only, "
                    f"{summary['tables_with_changes']} with changes."
                )
            except Exception as exc:  # noqa: BLE001 — an unreachable connector degrades this section, not the whole report
                logger.warning("[mapping_report] schema comparison unavailable: %s", exc)
                lines.append(f"_Not available — could not read live schema: {exc}_")

        lines.extend(["", "## Related Risk Findings", ""])
        try:
            from app.services.risk_service import RiskAggregationService

            # build-validation B-E15-08 investigated: this already scopes
            # to the mapping's source connection (below) and get_register's
            # own 30s TTL cache (risk_cache, keyed per DB — see
            # RISK_REGISTER_CACHE_TTL) means the expensive live-connector
            # sweep across every connection pair runs at most once per
            # cache window, not once per report call. connection_id here
            # only narrows the *returned* findings, not which adapters run
            # — pushing the filter into each adapter's own query would cut
            # the cold-cache cost further, but that touches shared E05
            # code (the Risk & Compliance Center UI and the E09 copilot
            # skill both depend on the unscoped path) and is a larger,
            # riskier refactor than this report's own scope justifies.
            register = RiskAggregationService(db).get_register(
                connection_id=source.id if source else None,
            )
            findings = register["findings"]
            if findings:
                for f in findings[:20]:
                    lines.append(f"- **{f['severity']}** ({f['category']}): {f['title']}")
                if len(findings) > 20:
                    lines.append(f"- _...and {len(findings) - 20} more — see the Risk & Compliance Center._")
            else:
                lines.append("No open risk findings for this mapping's source connection.")
        except Exception as exc:  # noqa: BLE001 — risk aggregation failing must not block the whole report
            logger.warning("[mapping_report] risk findings unavailable: %s", exc)
            lines.append(f"_Not available — {exc}_")

        logger.info("[mapping_report] stage=migration_report_generated mapping_id=%s", mapping_id)
        content = "\n".join(lines) + "\n"
        return {"content": content, "filename": f"mapping-{mapping_id}-migration-report.md"}
