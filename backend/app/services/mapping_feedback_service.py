"""AI Mapping Feedback & Training Loop (Enterprise v2, E14).

Scoped honestly as **heuristic memory, not a trained model** (per the E14
spec's own caveat): every accept/reject decision already lands on
`AISuggestion.status`; this module is a read-model over that existing
data, not a new feedback store. `HistoricalMatchService` answers "has this
exact (source_column, target_column) name pair been decided before,
anywhere?" and feeds a `historical_mapping` confidence contributor plus a
small ranking nudge. `FeedbackSummaryService` aggregates the same rows
into an acceptance-rate + most-corrected-pairs summary (E14-5).

There is no separate "Train Model" step (E14-4): both lookups always read
the live `ai_suggestions` table at request time, so there is nothing
stale to refresh. Presenting a "Train Model" button here would imply
model retraining that doesn't happen — see E14 spec for the full
rationale on why that task is intentionally not built.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.mapping import AISuggestion

DECIDED_STATUSES = ("accepted", "rejected")


class HistoricalMatchService:
    @staticmethod
    def lookup(db: Session, source_column: str, target_column: str) -> dict[str, Any]:
        """Nearest-neighbor lookup over past decisions for this exact
        (source_column, target_column) name pair, case-insensitive, across
        every mapping. Not scoped to one mapping — the point is to learn
        from the platform's whole history, not just this workspace."""
        rows = (
            db.query(AISuggestion.status)
            .filter(
                func.lower(AISuggestion.source_column) == source_column.lower(),
                func.lower(AISuggestion.target_column) == target_column.lower(),
                AISuggestion.status.in_(DECIDED_STATUSES),
            )
            .all()
        )
        accepted = sum(1 for (status,) in rows if status == "accepted")
        rejected = sum(1 for (status,) in rows if status == "rejected")
        total = accepted + rejected
        if total == 0:
            return {"sample_count": 0, "accepted_count": 0, "rejected_count": 0, "score": 0.0}
        return {
            "sample_count": total,
            "accepted_count": accepted,
            "rejected_count": rejected,
            "score": round((accepted / total) * 100.0, 1),
        }


class FeedbackSummaryService:
    @staticmethod
    def get_summary(db: Session, limit: int = 10) -> dict[str, Any]:
        decided = (
            db.query(
                AISuggestion.source_column, AISuggestion.target_column, AISuggestion.status,
            )
            .filter(AISuggestion.status.in_(DECIDED_STATUSES))
            .all()
        )
        total = len(decided)
        accepted = sum(1 for row in decided if row.status == "accepted")
        rejected = total - accepted
        acceptance_rate = round((accepted / total) * 100.0, 1) if total else 0.0

        groups: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"accepted": 0, "rejected": 0})
        for row in decided:
            key = (row.source_column, row.target_column)
            groups[key]["accepted" if row.status == "accepted" else "rejected"] += 1

        most_corrected = sorted(
            (
                {
                    "source_column": src, "target_column": tgt,
                    "accepted_count": counts["accepted"], "rejected_count": counts["rejected"],
                }
                for (src, tgt), counts in groups.items()
                if counts["rejected"] > 0
            ),
            key=lambda item: item["rejected_count"],
            reverse=True,
        )[:limit]

        return {
            "total_decided": total,
            "total_accepted": accepted,
            "total_rejected": rejected,
            "acceptance_rate": acceptance_rate,
            "most_corrected": most_corrected,
        }
