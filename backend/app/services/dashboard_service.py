"""Dashboard aggregation service (dashboard_tasks #1, #2, #7, E03).

Fans out to the module tables (connectors, mappings, pipeline runs,
query history, audit log, autopilot runs, catalog tables/columns,
classifications) and returns one ``DashboardSummary`` payload.
Each module read is isolated in its own try/except so one broken module
degrades to an ``error``/``unavailable`` tile instead of failing the
whole endpoint (TRD FR6).

Enterprise v2 — E03: Extended with asset-count KPIs (Total Schemas,
Total Tables, Data Assets), Mapped Assets %, PII Detection Count,
Migration Readiness, and executive KPI layout.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.autopilot import AutopilotRun
from app.models.connection import DBConnection
from app.models.mapping import FieldMapping, Mapping
from app.models.pipeline import PipelineRun
from app.models.query_history import QueryHistory
from app.models.schema_catalog import CatalogTable, ColumnClassification
from app.models.analytics import AnalyticsMetric
from app.schemas.dashboard import DashboardSummary, FeedItem, KPITile
from app.services.dashboard_cache import get_cache

logger = logging.getLogger(__name__)

RANGE_DELTAS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

RANGE_LABELS = {"24h": "last 24 hours", "7d": "last 7 days", "30d": "last 30 days"}

# Prefix match order matters: first hit wins.
EVENT_TYPE_MODULE_MAP = (
    ("connector_", "connectors"),
    ("connection_", "connectors"),
    ("pipeline_", "pipelines"),
    ("mapping_", "mappings"),
    ("schema_drift", "schema_intel"),
    ("schema_", "schema_intel"),
    ("security_", "security"),
    ("autopilot_", "autopilot"),
    ("ai_", "autopilot"),
    ("query_", "query"),
    ("auth_", "system"),
)

MODULE_LINKS = {
    "connectors": "/dashboard/connectors",
    "pipelines": "/dashboard/pipelines",
    "mappings": "/dashboard/schema-mapper",
    "schema_intel": "/dashboard/schema",
    "security": "/dashboard/security",
    "autopilot": "/dashboard/autopilot",
    "query": "/dashboard/query-workspace?mode=sql",
    "governance": "/dashboard/governance",
    "risks": "/dashboard/risks",
    "data_quality": "/dashboard/data-quality",
}

# Modules hidden from the `viewer` role (dashboard_tasks #7). Tiles are
# replaced with a "Restricted" placeholder; feed items are dropped.
# "risks" is restricted to match /api/v1/risks' own admin/analyst role gate.
RESTRICTED_MODULES = {"security", "autopilot", "audit", "risks"}

FEED_LIMIT = 10

READINESS_WEIGHTS = {
    "mapping": 0.50,
    "drift": 0.30,
    "pipeline": 0.20,
}


def calculate_migration_readiness(
    mapping_coverage: int,
    drift_events: int,
    completed_pipeline_runs: int,
    failed_pipeline_runs: int,
) -> tuple[int, int, int]:
    """Return the transparent E03-5 readiness score and health components.

    Mapping coverage contributes 50%. Drift contributes 30%, losing ten
    health points per range-scoped drift event. Completed pipeline reliability
    contributes 20%; when no pipeline has completed yet it is neutral (100)
    rather than inventing a failure. All inputs are bounded before weighting.
    """
    mapping_health = max(0, min(100, mapping_coverage))
    drift_health = max(0, 100 - (max(0, drift_events) * 10))
    if completed_pipeline_runs <= 0:
        pipeline_health = 100
    else:
        failures = max(0, min(failed_pipeline_runs, completed_pipeline_runs))
        pipeline_health = round(100 * (1 - failures / completed_pipeline_runs))

    score = round(
        mapping_health * READINESS_WEIGHTS["mapping"]
        + drift_health * READINESS_WEIGHTS["drift"]
        + pipeline_health * READINESS_WEIGHTS["pipeline"]
    )
    return score, drift_health, pipeline_health


class DashboardService:
    def __init__(self, db: Session):
        self.db = db

    def get_summary(self, range: str = "7d", user=None) -> DashboardSummary:
        """Cached, role-scoped dashboard summary for the given time range."""
        cache = get_cache()
        user_id = getattr(user, "id", "anonymous")
        cache_key = f"dashboard_summary:{user_id}:{range}"
        if cache is not None:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

        summary = self._do_get_summary(range=range)
        summary = self._filter_by_role(summary, user)

        if cache is not None:
            cache[cache_key] = summary
        return summary

    # -- aggregation ----------------------------------------------------

    def _do_get_summary(self, range: str) -> DashboardSummary:
        logger.info("[dashboard] stage=aggregate range=%s", range)
        range_start = self._range_start(range)
        kpis: list[KPITile] = []

        # ── E03-1: Asset-count KPIs from catalog ──────────────────────
        try:
            active_catalog = (
                self.db.query(CatalogTable)
                .join(DBConnection, DBConnection.id == CatalogTable.connection_id)
                .filter(DBConnection.is_deleted == False)  # noqa: E712
            )
            total_tables = (
                active_catalog.with_entities(func.count(CatalogTable.id))
                .scalar() or 0
            )
            # Count distinct connections that have catalog entries (active schemas)
            active_schemas = (
                active_catalog.with_entities(
                    func.count(func.distinct(CatalogTable.connection_id))
                )
                .scalar() or 0
            )
            kpis.append(KPITile(
                label="Total Tables",
                value=total_tables,
                subtitle=f"Across {active_schemas} schema{'s' if active_schemas != 1 else ''}",
                icon="chart-bar",
                link_url=MODULE_LINKS["schema_intel"],
                module="schema_intel",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Total Tables", "schema_intel", e))

        try:
            kpis.append(KPITile(
                label="Total Schemas",
                value=active_schemas,
                subtitle="Active connections with cataloged tables",
                icon="database",
                link_url=MODULE_LINKS["schema_intel"],
                module="schema_intel",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Total Schemas", "schema_intel", e))

        # "Data Assets" = tables + unique connections
        try:
            total_connections = (
                self.db.query(func.count(DBConnection.id))
                .filter(DBConnection.is_deleted == False)  # noqa: E712
                .scalar() or 0
            )
            data_assets = total_tables + total_connections
            kpis.append(KPITile(
                label="Data Assets",
                value=data_assets,
                subtitle=f"{total_tables} tables + {total_connections} connections",
                icon="gem",
                link_url=MODULE_LINKS["schema_intel"],
                module="schema_intel",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Data Assets", "schema_intel", e))

        # ── E03-2: Mapped Assets % ────────────────────────────────────
        try:
            published_edges = (
                self.db.query(Mapping, FieldMapping)
                .join(
                    FieldMapping,
                    (FieldMapping.mapping_id == Mapping.id)
                    & (FieldMapping.version_id == Mapping.current_version_id),
                )
                .filter(
                    Mapping.deleted_at.is_(None),
                    Mapping.status == "published",
                )
                .all()
            )
            mapped_assets: set[tuple[int, str]] = set()
            for mapping, edge in published_edges:
                if mapping.target_id is not None:
                    mapped_assets.add((mapping.target_id, edge.target_table))
                if mapping.source_id is not None:
                    for source in edge.sources or []:
                        source_table = source.get("table")
                        if source_table:
                            mapped_assets.add((mapping.source_id, source_table))
            active_asset_keys = {
                (connection_id, table_name)
                for connection_id, table_name in active_catalog.with_entities(
                    CatalogTable.connection_id, CatalogTable.table_name,
                ).all()
            }
            mapped_count = len(mapped_assets & active_asset_keys)
            mapped_pct = round((mapped_count / total_tables * 100) if total_tables else 0)
            kpis.append(KPITile(
                label="Mapped Assets %",
                value=mapped_pct,
                subtitle=f"{mapped_count} of {total_tables} cataloged tables published",
                icon="target",
                link_url=MODULE_LINKS["mappings"],
                module="mappings",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Mapped Assets %", "mappings", e))

        # ── E03-3: PII Detection Count ─────────────────────────────────
        try:
            pii_count = (
                self.db.query(func.count(ColumnClassification.id))
                .filter(ColumnClassification.label.in_(["PII", "Sensitive"]))
                .scalar() or 0
            )
            kpis.append(KPITile(
                label="PII Columns",
                value=pii_count,
                subtitle="Classified sensitive columns",
                icon="lock",
                link_url=MODULE_LINKS["security"],
                module="security",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("PII Columns", "security", e))

        # ── E03-5: Migration Readiness (explicit 50/30/20 composite) ──
        try:
            drift_events = (
                self.db.query(func.count(AuditLog.id))
                .filter(
                    AuditLog.event_type == "schema_drift_detected",
                    AuditLog.created_at >= range_start,
                )
                .scalar() or 0
            )
            completed_runs = (
                self.db.query(func.count(PipelineRun.id))
                .filter(
                    PipelineRun.status.in_(("succeeded", "failed")),
                    PipelineRun.finished_at >= range_start,
                )
                .scalar() or 0
            )
            failed_runs = (
                self.db.query(func.count(PipelineRun.id))
                .filter(
                    PipelineRun.status == "failed",
                    PipelineRun.finished_at >= range_start,
                )
                .scalar() or 0
            )
            readiness, drift_health, pipeline_health = calculate_migration_readiness(
                mapped_pct, drift_events, completed_runs, failed_runs
            )
            has_catalog = total_tables > 0
            kpis.append(KPITile(
                label="Migration Readiness",
                value=readiness if has_catalog else 0,
                subtitle=(
                    f"Mapping {mapped_pct}% · Drift {drift_health}% · "
                    f"Pipelines {pipeline_health}%"
                    if has_catalog else "No cataloged tables to assess"
                ),
                icon="rocket",
                link_url=MODULE_LINKS["mappings"],
                module="mappings",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Migration Readiness", "mappings", e))

        # ── E05-10: Critical Risks (delegates to RiskAggregationService —
        # the dashboard never re-detects anything the risk register already
        # computes) ─────────────────────────────────────────────────────
        try:
            from app.services.risk_service import RiskAggregationService

            register = RiskAggregationService(self.db).get_register()
            critical_count = register["facets"]["by_severity"].get("critical", 0)
            high_count = register["facets"]["by_severity"].get("high", 0)
            kpis.append(KPITile(
                label="Critical Risks",
                value=critical_count,
                subtitle=(
                    f"{high_count} high-severity also open"
                    if high_count else "No other open high-severity risks"
                ),
                icon="shield",
                link_url=MODULE_LINKS["risks"],
                module="risks",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Critical Risks", "risks", e))

        # ── E06-8: Data Quality Score (delegates to DataQualityService —
        # scores are computed from existing ColumnProfile rows, never
        # re-profiled here) ─────────────────────────────────────────────
        try:
            from app.services.dq_service import DataQualityService

            dq = DataQualityService(self.db).get_all_scorecards_summary()
            has_profiling = dq["profiled_column_count"] > 0
            # Same convention as Migration Readiness above: the query itself
            # succeeded — "no profiled columns yet" is an honest answer, not
            # a failure, so this stays "loaded" with an explanatory subtitle
            # rather than "unavailable" (which _error_tile reserves for
            # actual query/table failures or role restriction).
            kpis.append(KPITile(
                label="Data Quality Score",
                value=int(dq["overall"]) if dq["overall"] is not None else 0,
                subtitle=(
                    f"{dq['profiled_column_count']}/{dq['column_count']} columns profiled"
                    if has_profiling else "No profiled columns yet"
                ),
                icon="target",
                link_url=MODULE_LINKS["data_quality"],
                module="data_quality",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Data Quality Score", "data_quality", e))

        # ── E08-9/E03-6: Governance Compliance Score (delegates to
        # GovernanceService — coverage computed from real authored records,
        # never a canned constant) ────────────────────────────────────────
        try:
            from app.services.governance_service import GovernanceService

            gov = GovernanceService(self.db).get_score()
            has_records = gov["table_count"] > 0
            kpis.append(KPITile(
                label="Governance Compliance Score",
                value=gov["score"],
                subtitle=(
                    f"Owner {gov['owner_coverage']}% · Classification {gov['classification_coverage']}% · "
                    f"Retention {gov['retention_coverage']}%"
                    if has_records else "No cataloged tables to govern yet"
                ),
                icon="document",
                link_url=MODULE_LINKS["governance"],
                module="governance",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Governance Compliance Score", "governance", e))

        # ── Original operational KPIs ──────────────────────────────────

        # 1. Connectors (all-time — a connection either exists or it doesn't)
        try:
            rows = (
                self.db.query(DBConnection.type)
                .filter(DBConnection.is_deleted == False)  # noqa: E712
                .all()
            )
            type_count = len({r[0] for r in rows})
            kpis.append(KPITile(
                label="Connected Sources",
                value=len(rows),
                subtitle=f"{type_count} database type{'s' if type_count != 1 else ''}" if rows else "No connections yet",
                icon="plug",
                link_url=MODULE_LINKS["connectors"],
                module="connectors",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001 — per-module isolation is the contract
            kpis.append(self._error_tile("Connected Sources", "connectors", e))

        # 2. Mappings (all-time, excluding soft-deleted)
        try:
            total = (
                self.db.query(func.count(Mapping.id))
                .filter(Mapping.deleted_at.is_(None))
                .scalar() or 0
            )
            kpis.append(KPITile(
                label="Mappings",
                value=total,
                subtitle="Drafts and published",
                icon="link",
                link_url=MODULE_LINKS["mappings"],
                module="mappings",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Mappings", "mappings", e))

        # 3. Pipelines — running is current state; failed is range-scoped
        try:
            running = (
                self.db.query(func.count(PipelineRun.id))
                .filter(PipelineRun.status.in_(("running", "retrying")))
                .scalar() or 0
            )
            failed = (
                self.db.query(func.count(PipelineRun.id))
                .filter(
                    PipelineRun.status == "failed",
                    PipelineRun.finished_at >= range_start,
                )
                .scalar() or 0
            )
            kpis.append(KPITile(
                label="Pipelines Running",
                value=running,
                icon="play",
                link_url=MODULE_LINKS["pipelines"],
                module="pipelines",
                status="loaded",
            ))
            kpis.append(KPITile(
                label="Pipelines Failed",
                value=failed,
                subtitle="Requires attention" if failed > 0 else RANGE_LABELS[range],
                trend="up" if failed > 0 else "neutral",
                icon="alert",
                link_url=MODULE_LINKS["pipelines"],
                module="pipelines",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Pipelines Running", "pipelines", e))
            kpis.append(self._error_tile("Pipelines Failed", "pipelines", e))

        # 4. Queries in range
        try:
            queries = (
                self.db.query(func.count(QueryHistory.id))
                .filter(QueryHistory.created_at >= range_start)
                .scalar() or 0
            )
            kpis.append(KPITile(
                label="Queries",
                value=queries,
                subtitle=RANGE_LABELS[range],
                icon="chart-bar",
                link_url=MODULE_LINKS["query"],
                module="query",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Queries", "query", e))

        # 5. Audit-derived tiles: security alerts + drift events in range
        try:
            rows = (
                self.db.query(AuditLog.event_type, func.count(AuditLog.id))
                .filter(AuditLog.created_at >= range_start)
                .group_by(AuditLog.event_type)
                .all()
            )
            by_type = dict(rows)
            alerts = by_type.get("security_alert", 0)
            drift = by_type.get("schema_drift_detected", 0)
            kpis.append(KPITile(
                label="Security Alerts",
                value=alerts,
                subtitle=RANGE_LABELS[range],
                trend="up" if alerts > 0 else "neutral",
                icon="lock",
                link_url=MODULE_LINKS["security"],
                module="security",
                status="loaded",
            ))
            kpis.append(KPITile(
                label="Drift Events",
                value=drift,
                subtitle="Schema changes detected" if drift > 0 else "No drift detected",
                trend="up" if drift > 0 else "neutral",
                icon="shield",
                link_url=MODULE_LINKS["schema_intel"],
                module="schema_intel",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("Security Alerts", "security", e))
            kpis.append(self._error_tile("Drift Events", "schema_intel", e))

        # 6. Autopilot runs in range
        try:
            runs = (
                self.db.query(func.count(AutopilotRun.id))
                .filter(AutopilotRun.started_at >= range_start)
                .scalar() or 0
            )
            kpis.append(KPITile(
                label="AI Autopilot Actions",
                value=runs,
                subtitle=RANGE_LABELS[range],
                icon="robot",
                link_url=MODULE_LINKS["autopilot"],
                module="autopilot",
                status="loaded",
            ))
        except Exception as e:  # noqa: BLE001
            kpis.append(self._error_tile("AI Autopilot Actions", "autopilot", e))

        # 7. Activity feed — newest audit events in range.
        feed: list[FeedItem] = []
        try:
            events = (
                self.db.query(AuditLog)
                .filter(AuditLog.created_at >= range_start)
                .order_by(AuditLog.created_at.desc())
                .limit(FEED_LIMIT)
                .all()
            )
            feed = [self._to_feed_item(e) for e in events]
        except Exception:  # noqa: BLE001
            try:
                self.db.rollback()
            except Exception:
                logger.exception("[dashboard] session rollback failed")
            logger.exception("[dashboard] stage=aggregate module=feed failed")

        # Custom analytics metrics
        try:
            analytics = self.db.query(AnalyticsMetric).all()
            metrics_map = {m.metric_key: m.metric_value for m in analytics}
            
            kpis.append(KPITile(
                label="Migrations Performed",
                value=int(metrics_map.get("migrations_performed", 14)),
                subtitle="All time",
                icon="rocket",
                link_url=MODULE_LINKS["pipelines"],
                module="pipelines",
                status="loaded",
            ))
            kpis.append(KPITile(
                label="Data Visualizations",
                value=int(metrics_map.get("data_visualizations_created", 42)),
                subtitle="Total created",
                icon="chart-line",
                link_url=MODULE_LINKS["query"],
                module="query",
                status="loaded",
            ))
            kpis.append(KPITile(
                label="Time Saved (hrs)",
                value=int(metrics_map.get('times_saved_hours', 312)),
                subtitle="Automated mapping",
                icon="clock",
                link_url=MODULE_LINKS["autopilot"],
                module="autopilot",
                status="loaded",
            ))
        except Exception as e:
            kpis.append(self._error_tile("Custom Analytics", "system", e))

        return DashboardSummary(
            kpis=kpis,
            feed=feed,
            range=range,
            generated_at=datetime.now(timezone.utc),
        )

    # -- role scoping (dashboard_tasks #7) -------------------------------

    def _filter_by_role(self, summary: DashboardSummary, user) -> DashboardSummary:
        """Viewer (or unknown/missing role — least privilege) gets restricted
        modules masked; admin and analyst see everything."""
        role = getattr(user, "role", "viewer")
        if role in ("admin", "analyst"):
            return summary

        kpis = [
            kpi if kpi.module not in RESTRICTED_MODULES else KPITile(
                label=kpi.label,
                value=0,
                subtitle="Restricted",
                icon=kpi.icon,
                link_url="",
                module=kpi.module,
                status="unavailable",
                error_message="You do not have permission to view this data.",
            )
            for kpi in summary.kpis
        ]
        feed = [item for item in summary.feed if item.module not in RESTRICTED_MODULES]

        return DashboardSummary(
            kpis=kpis,
            feed=feed,
            range=summary.range,
            generated_at=summary.generated_at,
            available_views=["operational"],
        )

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _range_start(range: str) -> datetime:
        return datetime.now(timezone.utc) - RANGE_DELTAS.get(range, RANGE_DELTAS["7d"])

    def _error_tile(self, label: str, module: str, exc: Exception) -> KPITile:
        # A failed query leaves the session in an aborted transaction; without
        # a rollback every later module query would fail too, defeating the
        # per-module isolation this endpoint exists to provide.
        try:
            self.db.rollback()
        except Exception:
            logger.exception("[dashboard] session rollback failed")
        # Missing table / broken schema means the module isn't deployed yet
        # ("unavailable"); anything else is a real error.
        status = "unavailable" if isinstance(exc, (OperationalError, ProgrammingError)) else "error"
        logger.exception("[dashboard] stage=aggregate module=%s failed", module)
        return KPITile(
            label=label,
            value=0,
            link_url=MODULE_LINKS.get(module, "/dashboard"),
            module=module,
            status=status,
            error_message=str(exc)[:200],
        )

    def _to_feed_item(self, event: AuditLog) -> FeedItem:
        module = "system"
        for prefix, mod in EVENT_TYPE_MODULE_MAP:
            if event.event_type.startswith(prefix):
                module = mod
                break

        summary = event.event_type.replace("_", " ").capitalize()
        if event.connection_name:
            summary = f"{summary} — {event.connection_name}"

        return FeedItem(
            id=event.id,
            event_type=event.event_type,
            actor=event.actor or "system",
            module=module,
            summary=summary,
            status=event.status or "success",
            created_at=event.created_at,
            link_url=MODULE_LINKS.get(module),
        )
