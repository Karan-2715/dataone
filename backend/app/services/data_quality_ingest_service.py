"""Persist and read data-quality results reported by the Databricks PySpark job."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models.data_quality import DataQualityRun, DataQualityTableResult
from app.schemas.data_quality import DQIngestResult, DQRunIn, DQSummary

logger = logging.getLogger(__name__)

DIMENSIONS = ("completeness", "uniqueness", "validity", "freshness")
UPLOAD_SOURCE = "upload"
FILE_RUN_SOURCES = (UPLOAD_SOURCE, "databricks-files")  # runs that describe uploaded files, not warehouse tables
SCOPES = ("all", "files", "tables")
TREND_POINTS = 14


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 1) if present else None


def table_overall(scores: dict[str, Optional[float]]) -> Optional[float]:
    return _mean(scores.get(d) for d in DIMENSIONS)


class DataQualityIngestService:
    def __init__(self, db: Session):
        self.db = db

    def ingest(self, payload: DQRunIn) -> DQIngestResult:
        logger.info(
            "[pipeline] stage=dq_ingest run_id=%s tables=%d", payload.run_id, len(payload.tables),
        )
        existing = self.db.query(DataQualityRun).filter(DataQualityRun.run_id == payload.run_id).first()
        replaced = existing is not None
        if existing is not None:
            # Job retries re-send the same run_id: replace, never double count.
            self.db.delete(existing)
            self.db.flush()

        table_rows = []
        for t in payload.tables:
            scores = {d: getattr(t, d) for d in DIMENSIONS}
            table_rows.append(DataQualityTableResult(
                table_name=t.table_name,
                row_count=t.row_count,
                column_count=t.column_count,
                last_modified=t.last_modified,
                issues=[i.model_dump() for i in t.issues],
                overall_score=table_overall(scores),
                **scores,
            ))

        run = DataQualityRun(
            run_id=payload.run_id,
            source=payload.source,
            catalog=payload.catalog,
            schema_name=payload.schema_name,
            table_count=len(table_rows),
            total_rows=sum(r.row_count or 0 for r in table_rows),
            overall_score=_mean(r.overall_score for r in table_rows),
            completed_at=payload.completed_at or datetime.now(timezone.utc),
            tables=table_rows,
            **{d: _mean(getattr(r, d) for r in table_rows) for d in DIMENSIONS},
        )
        self.db.add(run)
        self.db.commit()
        return DQIngestResult(
            run_id=run.run_id, table_count=run.table_count,
            overall_score=run.overall_score, replaced=replaced,
        )

    def _scheduled_and_upload_runs(self) -> list:
        """Latest run of each scheduled source (table-profiling notebook, file pipeline, ...) plus every upload run."""
        order = (DataQualityRun.completed_at.desc(), DataQualityRun.id.desc())
        sources = [r[0] for r in self.db.query(DataQualityRun.source).filter(DataQualityRun.source != UPLOAD_SOURCE).distinct()]
        scheduled = [
            self.db.query(DataQualityRun).filter(DataQualityRun.source == src).order_by(*order).first()
            for src in sources
        ]
        uploads = self.db.query(DataQualityRun).filter(DataQualityRun.source == UPLOAD_SOURCE).order_by(*order).all()
        return sorted(scheduled + uploads, key=lambda r: (r.completed_at, r.id), reverse=True)

    @staticmethod
    def _in_scope(run: DataQualityRun, scope: str) -> bool:
        is_file = run.source in FILE_RUN_SOURCES
        return scope == "all" or (scope == "files" and is_file) or (scope == "tables" and not is_file)

    @staticmethod
    def _merge_tables(runs: list) -> list:
        """One row per table_name across ``runs``; the newer run wins."""
        merged: dict = {}
        for run in runs:
            for t in run.tables:
                current = merged.get(t.table_name)
                if current is None or run.completed_at > current[0]:
                    merged[t.table_name] = (run.completed_at, t)
        return [t for _, t in merged.values()]

    def get_summary(self, table_limit: int, ingest_configured: bool, scope: str = "all") -> DQSummary:
        """Headline over the latest run of each scheduled source merged with the latest upload run per table.

        Quick Uploads report one run each (``source="upload"``); showing only the newest run overall would let a
        single upload hide the job's results, so everything is combined. When a table was profiled more than once
        the more recent result wins. ``scope`` narrows the view to ``files`` (uploads + the file pipeline) or
        ``tables`` (the table-profiling job); ``scope_counts`` always reports all three so the UI can label them.
        """
        if scope not in SCOPES:
            scope = "all"
        all_runs = self._scheduled_and_upload_runs()
        scope_counts = {s: len(self._merge_tables([r for r in all_runs if self._in_scope(r, s)])) for s in SCOPES}
        runs = [r for r in all_runs if self._in_scope(r, scope)]
        if not runs:
            return DQSummary(latest=None, tables=[], trend=[], ingest_configured=ingest_configured,
                             scope=scope, scope_counts=scope_counts)

        rows = self._merge_tables(runs)

        def mean_of(attr: str) -> Optional[float]:
            return _mean(getattr(r, attr) for r in rows)

        catalogs = {r.catalog for r in runs if r.catalog}
        head = next((r for r in runs if r.source != UPLOAD_SOURCE), runs[0])  # prefer a scheduled run over an upload
        latest = {
            "run_id": head.run_id,
            "source": head.source,
            "catalog": catalogs.pop() if len(catalogs) == 1 else None,
            "schema_name": None,
            "table_count": len(rows),
            "total_rows": sum(r.row_count or 0 for r in rows),
            "overall_score": _mean(r.overall_score for r in rows),
            "completeness": mean_of("completeness"),
            "uniqueness": mean_of("uniqueness"),
            "validity": mean_of("validity"),
            "freshness": mean_of("freshness"),
            "completed_at": max(r.completed_at for r in runs),
        }

        # Worst tables first; tables with no measurable score sort last.
        tables = sorted(
            rows,
            key=lambda t: (t.overall_score is None, t.overall_score if t.overall_score is not None else 0),
        )[:table_limit]

        # Trend follows the scheduled sources in scope; with none (uploads only) it falls back to the upload runs.
        scheduled_sources = sorted({r.source for r in runs if r.source != UPLOAD_SOURCE})
        trend_query = self.db.query(DataQualityRun)
        trend_query = trend_query.filter(DataQualityRun.source.in_(scheduled_sources or [UPLOAD_SOURCE]))
        history = trend_query.order_by(DataQualityRun.completed_at.desc(), DataQualityRun.id.desc()).limit(TREND_POINTS).all()
        return DQSummary.model_validate({
            "latest": latest,
            "tables": tables,
            "trend": [
                {"completed_at": r.completed_at, "overall_score": r.overall_score}
                for r in reversed(history)
            ],
            "ingest_configured": ingest_configured,
            "scope": scope,
            "scope_counts": scope_counts,
        }, from_attributes=True)
