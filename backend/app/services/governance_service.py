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
import re
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.models.governance import CLASSIFICATION_LABELS, COMPLIANCE_STATUSES, GovernanceMetadata
from app.models.connection import DBConnection
from app.models.schema_catalog import CatalogTable
from app.models.schema_snapshot import SchemaSnapshot

logger = logging.getLogger(__name__)

_CLASSIFIED_MARKERS = {
    "ssn", "social_security", "national_id", "passport", "tax_id",
    "credit_card", "card_number", "cvv", "password", "passwd", "secret",
    "api_key", "access_token",
}
_SENSITIVE_MARKERS = {
    "email", "phone", "telephone", "mobile", "dob", "date_of_birth",
    "birth_date", "address", "full_name", "first_name", "last_name",
    "ip_address",
}
_CONFIDENTIAL_MARKERS = {
    "salary", "wage", "revenue", "profit", "cost", "price", "amount",
    "balance", "account", "order", "transaction", "invoice", "customer",
    "employee", "internal",
}


def _metadata_terms(values: list[str]) -> set[str]:
    terms: set[str] = set()
    for value in values:
        normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        terms.add(normalized)
        terms.update(part for part in normalized.split("_") if part)
    return terms


def classify_dataset(table_name: str, column_names: list[str]) -> str:
    """Assign a conservative dataset label from names only, never row data."""
    terms = _metadata_terms([table_name, *column_names])
    for label, markers in (
        ("Classified", _CLASSIFIED_MARKERS),
        ("Sensitive", _SENSITIVE_MARKERS),
        ("Confidential", _CONFIDENTIAL_MARKERS),
    ):
        if any(marker in terms for marker in markers):
            return label
    return "Public"


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

    def _asset_index(self, connection_id: int) -> dict[str, list[str]]:
        """Merge the normalized catalog with the complete cached schema."""
        assets: dict[str, list[str]] = {}
        catalog_tables = (
            self.db.query(CatalogTable)
            .options(selectinload(CatalogTable.columns))
            .filter(CatalogTable.connection_id == connection_id)
            .order_by(CatalogTable.table_name)
            .all()
        )
        for table in catalog_tables:
            assets[table.table_name] = [column.column_name for column in table.columns]

        snapshot = (
            self.db.query(SchemaSnapshot)
            .filter(SchemaSnapshot.connection_id == connection_id)
            .first()
        )
        for table_name, columns in ((snapshot.schema_json or {}) if snapshot else {}).items():
            if table_name.endswith(".(empty_schema)"):
                continue
            column_names = []
            if isinstance(columns, list):
                for column in columns:
                    if isinstance(column, dict):
                        name = column.get("name") or column.get("column_name")
                        if name:
                            column_names.append(str(name))
            if table_name not in assets or not assets[table_name]:
                assets[table_name] = column_names
        return dict(sorted(assets.items()))

    def _metadata_index(
        self, connection_ids: list[int],
    ) -> dict[tuple[int, str | None, str | None], GovernanceMetadata]:
        if not connection_ids:
            return {}
        rows = (
            self.db.query(GovernanceMetadata)
            .filter(GovernanceMetadata.connection_id.in_(connection_ids))
            .all()
        )
        return {
            (row.connection_id, row.table_name, row.column_name): row
            for row in rows
        }

    @staticmethod
    def _effective_from_index(
        index: dict[tuple[int, str | None, str | None], GovernanceMetadata],
        connection_id: int,
        table_name: str,
    ) -> GovernanceMetadata | None:
        return (
            index.get((connection_id, table_name, None))
            or index.get((connection_id, None, None))
        )

    def list_assets(self, connection_id: int) -> list[dict[str, Any]]:
        assets = []
        metadata = self._metadata_index([connection_id])
        for table_name, column_names in self._asset_index(connection_id).items():
            effective = self._effective_from_index(metadata, connection_id, table_name)
            saved_classification = effective.classification if effective else None
            classification = "Classified" if saved_classification == "PII" else saved_classification
            assets.append({
                "table_name": table_name,
                "owner": effective.owner if effective else None,
                "steward": effective.steward if effective else None,
                "classification": classification or classify_dataset(table_name, column_names),
                "classification_source": "manual" if saved_classification else "automatic",
                "retention_policy": effective.retention_policy if effective else None,
                "compliance_status": effective.compliance_status if effective else "not_assessed",
            })
        return assets

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
        connection_query = self.db.query(DBConnection).filter(DBConnection.is_deleted.is_(False))
        if connection_id is not None:
            connection_query = connection_query.filter(DBConnection.id == connection_id)
        connections = connection_query.all()
        metadata = self._metadata_index([connection.id for connection in connections])
        tables = [
            (connection.id, table_name)
            for connection in connections
            for table_name in self._asset_index(connection.id)
        ]
        if not tables:
            return {"score": 0, "table_count": 0, "owner_coverage": 0,
                    "classification_coverage": 0, "retention_coverage": 0}

        has_owner = has_classification = has_retention = 0
        for asset_connection_id, table_name in tables:
            effective = self._effective_from_index(metadata, asset_connection_id, table_name)
            if effective and effective.owner:
                has_owner += 1
            # Every discovered asset receives an automatic classification;
            # a saved manual value takes precedence in list_assets().
            has_classification += 1
            if effective and effective.retention_policy:
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
