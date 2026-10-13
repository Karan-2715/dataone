"""Profile one Unity Catalog table directly from the Databricks App.

This path is intentionally independent of the optional workspace installer/job:
the app already has SQL warehouse access and can persist a focused result in the
same request that the dashboard starts.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.models.connection import DBConnection
from app.schemas.data_quality import DQIssue, DQRunIn, DQTableIn
from app.services.schema_service import SchemaService, get_connector


class CatalogQualityError(RuntimeError):
    pass


EMAIL_NAME = re.compile(r"e[-_]?mail", re.I)
PHONE_NAME = re.compile(r"(phone|mobile|fax)", re.I)
NON_NEGATIVE_NAME = re.compile(
    r"(^|_)(amount|price|cost|qty|quantity|salary|age|count|total|revenue)(_|$)", re.I,
)
PAST_DATE_NAME = re.compile(
    r"(created|updated|modified|ingested|loaded|order|birth|hire|signup|registered)", re.I,
)


def _q(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _pct(part: int | Decimal | float, whole: int | Decimal | float) -> float | None:
    return None if not whole else round(100.0 * float(part) / float(whole), 1)


def _is_string(data_type: str) -> bool:
    lowered = data_type.lower()
    return any(token in lowered for token in ("char", "string", "text"))


def _is_numeric(data_type: str) -> bool:
    lowered = data_type.lower()
    return any(token in lowered for token in ("int", "decimal", "numeric", "float", "double", "real"))


def _is_temporal(data_type: str) -> bool:
    lowered = data_type.lower()
    return "date" in lowered or "timestamp" in lowered


def profile_catalog_table(
    connection: DBConnection, catalog: str, schema_name: str, table_name: str,
) -> DQRunIn:
    fqn = f"{catalog}.{schema_name}.{table_name}"
    schema = SchemaService.get_full_schema(connection)
    columns = schema.get(fqn)
    if columns is None:
        raise CatalogQualityError(f"Table '{fqn}' is not present in the Unity Catalog metadata cache")
    if not columns:
        raise CatalogQualityError(f"Table '{fqn}' has no columns to profile")

    full_name = ".".join(_q(part) for part in (catalog, schema_name, table_name))
    aggregates = ["COUNT(*) AS __rows"]
    rules: list[tuple[str, str, str]] = []
    for index, column in enumerate(columns):
        name = str(column.get("name") or column.get("column_name") or "")
        data_type = str(column.get("type") or column.get("data_type") or "")
        if not name:
            raise CatalogQualityError(f"Table '{fqn}' contains a column without a name")
        quoted = _q(name)
        present = f"{quoted} IS NOT NULL"
        if _is_string(data_type):
            present += f" AND TRIM(CAST({quoted} AS STRING)) <> ''"
        aggregates.append(f"SUM(CASE WHEN {present} THEN 1 ELSE 0 END) AS p{index}")

        condition = None
        rule_name = None
        if _is_string(data_type) and EMAIL_NAME.search(name):
            rule_name = "valid_email"
            condition = f"CAST({quoted} AS STRING) RLIKE '^[^@ ]+@[^@ ]+[.][^@ ]+$'"
        elif _is_string(data_type) and PHONE_NAME.search(name):
            rule_name = "valid_phone"
            condition = f"CAST({quoted} AS STRING) RLIKE '^\\+?[0-9][0-9 ()\\-]{{6,19}}$'"
        elif _is_numeric(data_type) and NON_NEGATIVE_NAME.search(name):
            rule_name = "non_negative"
            condition = f"{quoted} >= 0"
        elif _is_temporal(data_type) and PAST_DATE_NAME.search(name):
            rule_name = "not_in_future"
            condition = f"CAST({quoted} AS TIMESTAMP) <= current_timestamp() + INTERVAL 1 DAY"
        if condition and rule_name:
            rule_index = len(rules)
            aggregates.append(f"SUM(CASE WHEN {quoted} IS NOT NULL THEN 1 ELSE 0 END) AS vn{rule_index}")
            aggregates.append(
                f"SUM(CASE WHEN {quoted} IS NOT NULL AND ({condition}) THEN 1 ELSE 0 END) AS vp{rule_index}"
            )
            rules.append((name, rule_name, condition))

    connector = get_connector(connection)
    try:
        rows = connector.execute_query(f"SELECT {', '.join(aggregates)} FROM {full_name}")
        if not rows:
            raise CatalogQualityError(f"Databricks returned no profile for '{fqn}'")
        stats: dict[str, Any] = rows[0]
        total = int(stats.get("__rows") or 0)

        distinct_rows = 0
        if total:
            distinct = connector.execute_query(
                f"SELECT COUNT(*) AS distinct_rows FROM (SELECT DISTINCT * FROM {full_name}) dq_distinct"
            )
            if not distinct:
                raise CatalogQualityError(f"Databricks returned no uniqueness profile for '{fqn}'")
            distinct_rows = int(distinct[0].get("distinct_rows") or 0)
    finally:
        connector.close()

    issues: list[DQIssue] = []
    present_counts = [int(stats.get(f"p{i}") or 0) for i in range(len(columns))]
    completeness = _pct(sum(present_counts), total * len(columns))
    if total:
        for column, present in zip(columns, present_counts):
            column_score = _pct(present, total)
            if column_score is not None and column_score < 95:
                name = str(column.get("name") or column.get("column_name"))
                issues.append(DQIssue(
                    column=name, check="completeness",
                    detail=f"{column_score}% of values present ({total - present} missing)",
                ))

    uniqueness = _pct(distinct_rows, total)
    if uniqueness is not None and distinct_rows < total:
        duplicates = total - distinct_rows
        issues.append(DQIssue(
            column=None, check="uniqueness",
            detail=f"{duplicates} duplicate rows ({100 - uniqueness:.1f}%)",
        ))

    checked = passed = 0
    for index, (column_name, rule_name, _) in enumerate(rules):
        rule_checked = int(stats.get(f"vn{index}") or 0)
        rule_passed = int(stats.get(f"vp{index}") or 0)
        checked += rule_checked
        passed += rule_passed
        rule_score = _pct(rule_passed, rule_checked)
        if rule_score is not None and rule_score < 99:
            issues.append(DQIssue(
                column=column_name, check=rule_name,
                detail=f"{rule_score}% valid ({rule_checked - rule_passed} of {rule_checked} fail {rule_name})",
            ))

    source_hash = hashlib.sha256(fqn.encode("utf-8")).hexdigest()[:16]
    return DQRunIn(
        run_id=f"catalog-quality:{uuid.uuid4()}",
        source=f"databricks-manual:{source_hash}",
        catalog=catalog,
        schema_name=schema_name,
        completed_at=datetime.now(timezone.utc),
        tables=[DQTableIn(
            table_name=fqn,
            row_count=total,
            column_count=len(columns),
            completeness=completeness,
            uniqueness=uniqueness,
            validity=_pct(passed, checked),
            freshness=None,
            issues=issues[:20],
        )],
    )
