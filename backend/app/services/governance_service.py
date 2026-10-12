"""Governance Intelligence Center (Enterprise v2, E08).

Authoring/read service for per-asset governance metadata (owner,
steward, classification, retention, compliance status) and a coverage
score computed from real records — never a canned constant. See
app/models/governance.py for why this replaces `dama_metadata`.

Enforcement (acting on retention policy) and compliance reporting
(framework mapping) are explicitly NOT here — those are E08-7/E08-8,
gated on tenant isolation and legal sign-off respectively.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.governance import CLASSIFICATION_LABELS, COMPLIANCE_STATUSES, GovernanceMetadata
from app.models.connection import DBConnection
from app.models.schema_catalog import CatalogTable

logger = logging.getLogger(__name__)


class GovernanceService:
    def __init__(self, db: Session):
        self.db = db

    def get_effective(self, connection_id: int, table: str | None = None,
                      column: str | None = None) -> GovernanceMetadata | None:
        """Most specific record first (column > table > connection-wide) —
        a coarser record is the fallback for an asset with no record of
        its own, not an error."""
        candidates = []
        if column is not None and table is not None:
            candidates.append((table, column))
        if table is not None:
            candidates.append((table, None))
        candidates.append((None, None))

        for tbl, col in candidates:
            row = (
                self.db.query(GovernanceMetadata)
                .filter(
                    GovernanceMetadata.connection_id == connection_id,
                    GovernanceMetadata.table_name == tbl,
                    GovernanceMetadata.column_name == col,
                )
                .first()
            )
            if row is not None:
                return row
        return None

    def list_for_connection(self, connection_id: int) -> list[GovernanceMetadata]:
        return (
            self.db.query(GovernanceMetadata)
            .filter(GovernanceMetadata.connection_id == connection_id)
            .order_by(GovernanceMetadata.table_name.is_(None).desc(),
                     GovernanceMetadata.table_name, GovernanceMetadata.column_name)
            .all()
        )

    def upsert(
        self, connection_id: int, table: str | None, column: str | None,
        owner: str | None, steward: str | None, classification: str | None,
        retention_policy: str | None, compliance_status: str | None, updated_by: str,
    ) -> GovernanceMetadata:
        if classification is not None and classification not in CLASSIFICATION_LABELS:
            raise ValueError(f"classification must be one of {CLASSIFICATION_LABELS}")
        if compliance_status is not None and compliance_status not in COMPLIANCE_STATUSES:
            raise ValueError(f"compliance_status must be one of {COMPLIANCE_STATUSES}")

        row = (
            self.db.query(GovernanceMetadata)
            .filter(
                GovernanceMetadata.connection_id == connection_id,
                GovernanceMetadata.table_name == table,
                GovernanceMetadata.column_name == column,
            )
            .first()
        )
        if row is None:
            row = GovernanceMetadata(connection_id=connection_id, table_name=table, column_name=column)
            self.db.add(row)

        row.owner = owner
        row.steward = steward
        row.classification = classification
        row.retention_policy = retention_policy
        if compliance_status is not None:
            row.compliance_status = compliance_status
        row.updated_by = updated_by
        self.db.commit()
        self.db.refresh(row)
        logger.info("[governance] stage=upsert connection_id=%s table=%s column=%s by=%s",
                    connection_id, table, column, updated_by)
        return row

    def get_score(self, connection_id: int | None = None) -> dict[str, Any]:
        """Coverage score: % of cataloged tables with an owner, a
        classification, and a retention policy (via get_effective's
        connection-level fallback) — averaged equally. Never a canned
        number; an empty catalog returns 0 with an honest explanation,
        matching the Migration Readiness / DQ Score precedent."""
        query = (
            self.db.query(CatalogTable)
            .join(DBConnection, DBConnection.id == CatalogTable.connection_id)
            .filter(DBConnection.is_deleted.is_(False))
        )
        if connection_id is not None:
            query = query.filter(CatalogTable.connection_id == connection_id)
        tables = query.all()
        if not tables:
            return {"score": 0, "table_count": 0, "owner_coverage": 0,
                    "classification_coverage": 0, "retention_coverage": 0}

        has_owner = has_classification = has_retention = 0
        for table in tables:
            effective = self.get_effective(table.connection_id, table.table_name)
            if effective is None:
                continue
            if effective.owner:
                has_owner += 1
            if effective.classification:
                has_classification += 1
            if effective.retention_policy:
                has_retention += 1

        total = len(tables)
        owner_pct = round(100 * has_owner / total)
        classification_pct = round(100 * has_classification / total)
        retention_pct = round(100 * has_retention / total)
        score = round((owner_pct + classification_pct + retention_pct) / 3)

        return {
            "score": score,
            "table_count": total,
            "owner_coverage": owner_pct,
            "classification_coverage": classification_pct,
            "retention_coverage": retention_pct,
        }
