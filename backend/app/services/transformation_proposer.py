"""Transformation proposal using the EXISTING TransformationPayload grammar
(agentic_dba_tasks #5) — direct/cast/concat/coalesce/... from
transformation_grammar.py. No new transformation DSL: Schema Mapper's
editor, validation, and execution already understand this one.

A relationship the grammar genuinely can't express is left unset with an
explicit note (manual authoring in Schema Mapper after task #8's draft
mapping is created) — false confidence is worse than an honest gap.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.schema_catalog import CatalogColumn, CatalogTable, ColumnProfile
from app.services.transformation_grammar import SQL_TYPES, validate

logger = logging.getLogger(__name__)

# Type-family normalization, mirroring mapping_validation_service's families.
_TYPE_FAMILIES: Dict[str, str] = {
    "TEXT": "text", "VARCHAR": "text", "CHAR": "text", "CLOB": "text", "STRING": "text",
    "INTEGER": "integer", "INT": "integer", "BIGINT": "integer",
    "SMALLINT": "integer", "TINYINT": "integer",
    "FLOAT": "float", "DOUBLE": "float", "REAL": "float",
    "DECIMAL": "float", "NUMERIC": "float", "NUMBER": "float",
    "DATE": "datetime", "TIMESTAMP": "datetime", "DATETIME": "datetime",
    "TIMESTAMPTZ": "datetime",
    "BOOLEAN": "bool", "BOOL": "bool",
}


def _base_type(raw: Optional[str]) -> Optional[str]:
    """'VARCHAR(50)' -> 'VARCHAR'; None-safe."""
    if not raw:
        return None
    m = re.match(r"\s*([A-Za-z]+)", raw)
    return m.group(1).upper() if m else None


def _family(raw: Optional[str]) -> Optional[str]:
    base = _base_type(raw)
    return _TYPE_FAMILIES.get(base) if base else None


def _source_nullable(db: Session, connection_id: int,
                     table: str, column: str) -> Tuple[Optional[bool], Optional[float]]:
    row = (
        db.query(CatalogColumn, ColumnProfile)
        .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
        .outerjoin(ColumnProfile, ColumnProfile.column_id == CatalogColumn.id)
        .filter(CatalogTable.connection_id == connection_id,
                CatalogTable.table_name == table,
                CatalogColumn.column_name == column)
        .first()
    )
    if row is None:
        return None, None
    col, profile = row
    return col.nullable, (profile.null_rate if profile else None)


def propose_transformations(
    db: Session,
    connection_id: int,
    proposed_tables: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Returns (transformations, confidence_notes).

    Each transformation entry: {target_table, target_column, target_type,
    target_nullable, sources: [{table, column}], transformation: dict|None,
    note: str|None}. transformation=None means the grammar can't express it.
    """
    out: List[Dict[str, Any]] = []
    notes: List[str] = []

    for table in proposed_tables:
        for col in table.get("columns", []):
            source_refs = col.get("source_refs") or []
            if not source_refs:
                continue

            entry: Dict[str, Any] = {
                "target_table": table["name"],
                "target_column": col["name"],
                "target_type": col.get("type"),
                "target_nullable": col.get("nullable", True),
                "sources": [{"table": s["table"], "column": s["column"]} for s in source_refs],
                "transformation": None,
                "note": None,
            }

            if len(source_refs) > 1:
                # N:1 — same multi-source concat convention Schema Mapper's
                # Canvas established (mapper_tasks #1): source parts with a
                # literal " " separator between them.
                parts: List[Dict[str, Any]] = []
                for i in range(len(source_refs)):
                    if i > 0:
                        parts.append({"kind": "literal", "value": " "})
                    parts.append({"kind": "source"})
                entry["transformation"] = {"kind": "concat", "parts": parts}
            else:
                src = source_refs[0]
                src_family = _family(src.get("type"))
                tgt_base = _base_type(col.get("type"))
                tgt_family = _TYPE_FAMILIES.get(tgt_base) if tgt_base else None

                if tgt_base and tgt_family is None:
                    # Unknown target type (e.g. GEOMETRY): the grammar can't
                    # express a conversion it doesn't understand — honest gap.
                    entry["note"] = (
                        f"target type {col.get('type')} isn't in the transformation "
                        f"grammar's known type families — author manually in Schema Mapper"
                    )
                    notes.append(f"{table['name']}.{col['name']}: {entry['note']}")
                    out.append(entry)
                    continue

                if src_family and tgt_family and src_family != tgt_family:
                    if tgt_base in SQL_TYPES:
                        entry["transformation"] = {
                            "kind": "cast",
                            "from": src.get("type") or "unknown",
                            "to": tgt_base,
                        }
                    else:
                        entry["note"] = (
                            f"type change {src.get('type')} → {col.get('type')} isn't "
                            f"expressible in the transformation grammar (target type not "
                            f"castable) — author manually in Schema Mapper"
                        )
                        notes.append(f"{table['name']}.{col['name']}: {entry['note']}")
                        out.append(entry)
                        continue
                else:
                    # Same family (or unknown types): null-handling upgrade —
                    # a nullable source feeding a NOT NULL target gets a
                    # coalesce fallback (task #4's null-handling as a
                    # transform), else plain direct.
                    src_nullable, null_rate = _source_nullable(
                        db, connection_id, src["table"], src["column"])
                    if col.get("nullable") is False and src_nullable and (null_rate or 0) > 0:
                        entry["transformation"] = {
                            "kind": "coalesce",
                            "fallback_kind": "literal",
                            "fallback_value": "",
                        }
                        entry["note"] = (
                            f"source {src['table']}.{src['column']} has nulls "
                            f"({(null_rate or 0):.2%}) but target is NOT NULL — "
                            f"coalesce to '' proposed; review the fallback value"
                        )
                    else:
                        entry["transformation"] = {"kind": "direct"}

            if entry["transformation"] is not None:
                # Defense in depth: never emit a payload the grammar rejects.
                try:
                    validate(entry["transformation"])
                except Exception as exc:  # GrammarError
                    logger.warning("[agentic_dba] proposed transformation invalid: %s", exc)
                    entry["note"] = f"proposed transformation failed grammar validation: {exc}"
                    entry["transformation"] = None
                    notes.append(f"{table['name']}.{col['name']}: {entry['note']}")

            out.append(entry)

    return out, notes
