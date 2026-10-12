"""
Shared SQL Statement Classifier.

Classifies a SQL statement as SELECT / INSERT / UPDATE / DELETE / DDL / UNKNOWN
and extracts metadata (tables referenced). Used by both Query Studio (QS-T2)
and AskData Bot (ADB-T2) guardrails.
"""

import enum
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import sqlparse
from sqlparse.sql import Identifier, IdentifierList, Statement, Token
from sqlparse.tokens import DML, DDL, Keyword, Name

logger = logging.getLogger(__name__)


class StatementType(enum.Enum):
    SELECT = "select"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    DDL = "ddl"  # CREATE, ALTER, DROP, TRUNCATE
    SHOW = "show"  # SHOW DATABASES, SHOW TABLES, DESCRIBE, USE — safe metadata queries
    UNKNOWN = "unknown"


_WRITE_KEYWORDS = {"insert", "update", "delete", "merge", "upsert"}
_DDL_KEYWORDS = {"create", "alter", "drop", "truncate", "rename", "comment", "grant", "revoke"}
_SHOW_KEYWORDS = {"show", "describe", "desc", "use", "explain"}  # safe read-only metadata ops


@dataclass
class ClassifiedStatement:
    type: StatementType
    raw_sql: str
    tables_referenced: List[str] = field(default_factory=list)
    is_multi_statement: bool = False
    warnings: List[str] = field(default_factory=list)


def classify(sql: str) -> ClassifiedStatement:
    """Classify a SQL statement by type and extract referenced tables.

    Uses sqlparse for robust parsing. Returns the most restrictive type
    when multiple statements are present (e.g. a SELECT + INSERT classifies
    as INSERT).
    """
    if not sql or not sql.strip():
        return ClassifiedStatement(
            type=StatementType.UNKNOWN,
            raw_sql=sql,
            warnings=["Empty SQL statement"],
        )

    parsed = sqlparse.parse(sql)

    if not parsed:
        return ClassifiedStatement(
            type=StatementType.UNKNOWN,
            raw_sql=sql,
            warnings=["Could not parse SQL"],
        )

    is_multi = len(parsed) > 1
    overall_type = StatementType.UNKNOWN
    all_tables: List[str] = []
    warnings: List[str] = []

    for stmt in parsed:
        stmt_type = _classify_single(stmt)
        tables = _extract_tables(stmt)
        all_tables.extend(tables)

        # Most restrictive type wins
        if _is_more_restrictive(stmt_type, overall_type):
            overall_type = stmt_type

        if stmt_type == StatementType.UNKNOWN:
            warnings.append(f"Unrecognized statement type")

    if is_multi:
        warnings.append("Multiple statements detected")

    # Deduplicate tables while preserving order
    seen = set()
    unique_tables: List[str] = []
    for t in all_tables:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique_tables.append(t)

    return ClassifiedStatement(
        type=overall_type,
        raw_sql=sql,
        tables_referenced=unique_tables,
        is_multi_statement=is_multi,
        warnings=warnings,
    )


def _classify_single(stmt: Statement) -> StatementType:
    """Classify a single SQL statement by examining its first token."""
    stmt_str = stmt.value.strip().lower()

    # Check for CTE (WITH clause)
    if stmt_str.startswith("with "):
        # Walk tokens to find the main statement after WITH
        found_select = False
        for token in stmt.tokens:
            if token.is_group:
                for subtoken in token.flatten():
                    if subtoken.ttype is DML and subtoken.value.lower() == "select":
                        found_select = True
                        break
        if found_select:
            return StatementType.SELECT
        # Fall through to normal classification

    for token in stmt.tokens:
        if token.is_keyword:
            kw = token.value.lower()
            if kw in _WRITE_KEYWORDS:
                if kw == "insert":
                    return StatementType.INSERT
                elif kw == "update":
                    return StatementType.UPDATE
                elif kw == "delete":
                    return StatementType.DELETE
                return StatementType.UNKNOWN
            if kw in _DDL_KEYWORDS:
                return StatementType.DDL
        if token.ttype is DML:
            if token.value.lower() == "select":
                return StatementType.SELECT
            if token.value.lower() in _WRITE_KEYWORDS:
                return {
                    "insert": StatementType.INSERT,
                    "update": StatementType.UPDATE,
                    "delete": StatementType.DELETE,
                }.get(token.value.lower(), StatementType.UNKNOWN)
        if token.ttype is DDL:
            return StatementType.DDL

    # If we couldn't classify definitively, check the raw string
    first_word = stmt_str.split()[0] if stmt_str.split() else ""
    if first_word == "select":
        return StatementType.SELECT
    if first_word in _SHOW_KEYWORDS:
        return StatementType.SHOW
    if first_word in _WRITE_KEYWORDS:
        return {
            "insert": StatementType.INSERT,
            "update": StatementType.UPDATE,
            "delete": StatementType.DELETE,
        }.get(first_word, StatementType.UNKNOWN)
    if first_word in _DDL_KEYWORDS:
        return StatementType.DDL
    if first_word.startswith("with"):
        # CTE that we couldn't resolve above
        if "select" in stmt_str:
            return StatementType.SELECT

    return StatementType.UNKNOWN


def _extract_tables(stmt: Statement) -> List[str]:
    """Extract table names from FROM, JOIN, INTO, TABLE clauses."""
    tables: List[str] = []
    stmt_str = stmt.value.strip().lower()

    # Handle edge case: no FROM/JOIN clause
    if not any(kw in stmt_str for kw in ("from ", "join ", "into ", "table ", "update ")):
        return tables

    for token in stmt.tokens:
        if token.is_group:
            _extract_tables_from_group(token, tables)
        elif isinstance(token, Identifier):
            _add_table(token, tables)

    return tables


def _extract_tables_from_group(token, tables: List[str]) -> None:
    """Recursively extract table names from token groups."""
    for subtoken in token.tokens:
        if subtoken.is_group:
            _extract_tables_from_group(subtoken, tables)
        elif isinstance(subtoken, Identifier):
            _add_table(subtoken, tables)
        elif subtoken.ttype is Keyword and subtoken.value.lower() in (
            "from", "into", "table", "update", "join",
        ):
            # Next identifier should be a table name
            pass


def _add_table(token: Identifier, tables: List[str]) -> None:
    """Add a table identifier to the list, handling aliases."""
    name = token.get_real_name()
    if name:
        tables.append(name)
    else:
        # Fall back to raw value
        val = str(token.value).strip()
        # Skip subqueries and function calls
        if not val.startswith("(") and "(" not in val:
            # Remove surrounding quotes
            val = val.strip('"').strip("'").strip("`")
            if val:
                tables.append(val)


def _is_more_restrictive(new: StatementType, current: StatementType) -> bool:
    """Determine if new type is more 'restrictive' than current.

    Restrictiveness hierarchy: DDL > INSERT/UPDATE/DELETE > SELECT > UNKNOWN
    """
    rank = {
        StatementType.UNKNOWN: 0,
        StatementType.SELECT: 1,
        StatementType.SHOW: 1,  # SHOW/DESCRIBE are equally safe as SELECT
        StatementType.INSERT: 2,
        StatementType.UPDATE: 2,
        StatementType.DELETE: 2,
        StatementType.DDL: 3,
    }
    return rank.get(new, 0) > rank.get(current, 0)