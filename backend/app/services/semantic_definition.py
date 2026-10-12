"""Semantic definition language (DP-SEM-001, SEM-T1 full).

A restricted, allow-list grammar for metric definitions. Mirrors the
pattern established by app/services/transformation_grammar.py for the
Schema Mapper transformation grammar — no freeform SQL, structured
JSON payloads, every field validated against an allow-list, a
``compile_sql`` method that renders a parameterized SQL fragment.

Design promise (per TRD §11 Constraints): governed definitions only;
no free SQL; every allowed kind has a fixed payload shape; bad input
fails with a structured GrammarError instead of silently producing
questionable SQL.

Allowed kinds (all enforced at parse() time):

  aggregation     — one of sum, count, count_distinct, avg, min, max
  filter op       — one of =, !=, <, <=, >, >=, in, not_in, is_null,
                   is_not_null
  join type       — one of inner, left, right, full
  time_grain      — one of day, week, month, quarter, year

Required fields:
  entity          — entity name (validated against the catalog by the
                   resolution engine in Task #5)
  measure         — measure name (validated against the catalog)
  aggregation     — one of the allowed aggregation strings

Optional fields:
  filters         — list of {column, op, value}
  joins           — list of {table, on: {left, right}, type}
  time_grain      — one of the allowed grain strings; requires time_column
  time_column     — required when time_grain is set; column to grain on
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


ALLOWED_AGGREGATIONS: frozenset = frozenset({
    "sum", "count", "count_distinct", "avg", "min", "max",
})
ALLOWED_FILTER_OPS: frozenset = frozenset({
    "=", "!=", "<", "<=", ">", ">=", "in", "not_in", "is_null", "is_not_null",
})
ALLOWED_JOIN_TYPES: frozenset = frozenset({"inner", "left", "right", "full"})
ALLOWED_TIME_GRAINS: frozenset = frozenset({
    "day", "week", "month", "quarter", "year",
})

# Filter ops whose value is treated as a single placeholder vs. an
# IN-list. is_null / is_not_null emit no value placeholder.
_SCALAR_OPS = {"=", "!=", "<", "<=", ">", ">="}
_LIST_OPS = {"in", "not_in"}
_NULL_OPS = {"is_null", "is_not_null"}


class GrammarError(ValueError):
    """Raised when a semantic definition payload is invalid.

    Mirrors transformation_grammar.GrammarError so the contract for
    callers is identical across the two grammars in this codebase.

    Attributes:
        kind: short error category ("unknown_field", "missing_field",
              "bad_type", "bad_enum")
        location: dotted path within the payload
    """

    def __init__(self, message: str, *, kind: str = "grammar_error",
                 location: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.location = location

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "message": str(self),
                "location": self.location}


def parse(payload: Any) -> Dict[str, Any]:
    """Validate a semantic definition payload and return a normalized dict.

    Same contract as transformation_grammar.parse: raises GrammarError on
    invalid input, returns a dict whose values are normalized (lowercase
    enum values, deduped filter list, etc.).
    """
    if not isinstance(payload, dict):
        raise GrammarError("definition must be a JSON object",
                           kind="bad_type", location="$")

    out: Dict[str, Any] = {}

    # --- Required: entity ---
    entity = payload.get("entity")
    if not isinstance(entity, str) or not entity:
        raise GrammarError("'entity' is required and must be a non-empty string",
                           kind="missing_field", location="entity")
    out["entity"] = entity

    # --- Required: measure ---
    measure = payload.get("measure")
    if not isinstance(measure, str) or not measure:
        raise GrammarError("'measure' is required and must be a non-empty string",
                           kind="missing_field", location="measure")
    out["measure"] = measure

    # --- Required: aggregation ---
    agg = payload.get("aggregation")
    if not isinstance(agg, str) or agg not in ALLOWED_AGGREGATIONS:
        raise GrammarError(
            f"'aggregation' must be one of {sorted(ALLOWED_AGGREGATIONS)}; got {agg!r}",
            kind="bad_enum", location="aggregation",
        )
    out["aggregation"] = agg

    # --- Optional: filters ---
    raw_filters = payload.get("filters", [])
    if raw_filters is None:
        raw_filters = []
    if not isinstance(raw_filters, list):
        raise GrammarError("'filters' must be a list",
                           kind="bad_type", location="filters")
    out["filters"] = [_parse_filter(f, i) for i, f in enumerate(raw_filters)]

    # --- Optional: joins ---
    raw_joins = payload.get("joins", [])
    if raw_joins is None:
        raw_joins = []
    if not isinstance(raw_joins, list):
        raise GrammarError("'joins' must be a list",
                           kind="bad_type", location="joins")
    out["joins"] = [_parse_join(j, i) for i, j in enumerate(raw_joins)]

    # --- Optional: time_grain + time_column (must come as a pair) ---
    grain = payload.get("time_grain")
    time_col = payload.get("time_column")
    if grain is not None or time_col is not None:
        if grain is None or time_col is None:
            raise GrammarError(
                "'time_grain' and 'time_column' must be set together",
                kind="bad_type", location="time_grain/time_column",
            )
        if grain not in ALLOWED_TIME_GRAINS:
            raise GrammarError(
                f"'time_grain' must be one of {sorted(ALLOWED_TIME_GRAINS)}; got {grain!r}",
                kind="bad_enum", location="time_grain",
            )
        if not isinstance(time_col, str) or not time_col:
            raise GrammarError(
                "'time_column' must be a non-empty string when time_grain is set",
                kind="bad_type", location="time_column",
            )
        out["time_grain"] = grain
        out["time_column"] = time_col

    return out


def _parse_filter(f: Any, idx: int) -> Dict[str, Any]:
    if not isinstance(f, dict):
        raise GrammarError(
            f"filters[{idx}] must be an object",
            kind="bad_type", location=f"filters[{idx}]",
        )
    col = f.get("column")
    op = f.get("op")
    if not isinstance(col, str) or not col:
        raise GrammarError(
            f"filters[{idx}].column is required and must be a non-empty string",
            kind="bad_type", location=f"filters[{idx}].column",
        )
    if not isinstance(op, str) or op not in ALLOWED_FILTER_OPS:
        raise GrammarError(
            f"filters[{idx}].op must be one of {sorted(ALLOWED_FILTER_OPS)}; got {op!r}",
            kind="bad_enum", location=f"filters[{idx}].op",
        )
    value = f.get("value")
    if op in _NULL_OPS:
        # is_null / is_not_null: value must be None or absent.
        if value is not None:
            raise GrammarError(
                f"filters[{idx}].value must be null for op '{op}'",
                kind="bad_type", location=f"filters[{idx}].value",
            )
        normalized_value = None
    elif op in _LIST_OPS:
        # in / not_in: value must be a list.
        if not isinstance(value, list):
            raise GrammarError(
                f"filters[{idx}].value must be a list for op '{op}'",
                kind="bad_type", location=f"filters[{idx}].value",
            )
        normalized_value = list(value)
    else:
        # Scalar ops: value can be anything except None.
        if value is None:
            raise GrammarError(
                f"filters[{idx}].value is required for op '{op}'",
                kind="missing_field", location=f"filters[{idx}].value",
            )
        normalized_value = value
    return {"column": col, "op": op, "value": normalized_value}


def _parse_join(j: Any, idx: int) -> Dict[str, Any]:
    if not isinstance(j, dict):
        raise GrammarError(
            f"joins[{idx}] must be an object",
            kind="bad_type", location=f"joins[{idx}]",
        )
    table = j.get("table")
    on = j.get("on")
    jtype = j.get("type", "inner")
    if not isinstance(table, str) or not table:
        raise GrammarError(
            f"joins[{idx}].table is required and must be a non-empty string",
            kind="bad_type", location=f"joins[{idx}].table",
        )
    if not isinstance(on, dict):
        raise GrammarError(
            f"joins[{idx}].on must be an object with 'left' and 'right' columns",
            kind="bad_type", location=f"joins[{idx}].on",
        )
    left = on.get("left")
    right = on.get("right")
    if not (isinstance(left, str) and left and isinstance(right, str) and right):
        raise GrammarError(
            f"joins[{idx}].on.left and joins[{idx}].on.right are required strings",
            kind="bad_type", location=f"joins[{idx}].on",
        )
    if jtype not in ALLOWED_JOIN_TYPES:
        raise GrammarError(
            f"joins[{idx}].type must be one of {sorted(ALLOWED_JOIN_TYPES)}; got {jtype!r}",
            kind="bad_enum", location=f"joins[{idx}].type",
        )
    return {"table": table, "on": {"left": left, "right": right}, "type": jtype}


# ── SQL compilation ────────────────────────────────────────────
# Task #5's resolution engine will turn this into a real executable
# query against the connected database. For now, compile_sql produces a
# parameterized SQL fragment + a placeholder list the caller binds.
# This is the same pattern transformation_grammar.compile_sql uses.

_TIME_GRAIN_SQL = {
    "day": "DATE_TRUNC('day', {col})",
    "week": "DATE_TRUNC('week', {col})",
    "month": "DATE_TRUNC('month', {col})",
    "quarter": "DATE_TRUNC('quarter', {col})",
    "year": "DATE_TRUNC('year', {col})",
}
# `DATE_TRUNC` is Postgres / SQLite syntax. The resolution engine (Task #5)
# will swap dialect-appropriate trunc functions at execution time; here
# we keep the surface dialect-neutral by emitting a placeholder the
# resolution engine can replace. For now: emit DATE_TRUNC and document.

_AGG_SQL = {
    "sum": "SUM({col})",
    "count": "COUNT({col})",
    "count_distinct": "COUNT(DISTINCT {col})",
    "avg": "AVG({col})",
    "min": "MIN({col})",
    "max": "MAX({col})",
}


def compile_sql(definition: Dict[str, Any],
                placeholders: List[Any]) -> str:
    """Render a validated definition into a parameterized SQL fragment.

    `placeholders` is appended to in source/filter value order; the
    caller binds them. This mirrors transformation_grammar.compile_sql.

    Output shape (illustrative):
        SELECT DATE_TRUNC('month', orders.created_at) AS bucket,
               SUM(orders.amount) AS value
          FROM orders
          LEFT JOIN customers ON orders.customer_id = customers.id
         WHERE orders.status = %s
           AND orders.created_at >= %s
         GROUP BY bucket
    """
    parsed = parse(definition)

    entity = parsed["entity"]
    measure = parsed["measure"]
    agg = parsed["aggregation"]
    grain = parsed.get("time_grain")
    time_col = parsed.get("time_column")
    filters = parsed["filters"]
    joins = parsed["joins"]

    # SELECT clause: time grain + aggregated measure
    measure_col = f"{entity}.{measure}"
    agg_expr = _AGG_SQL[agg].format(col=measure_col)
    if grain is not None:
        grain_col = f"{entity}.{time_col}"
        grain_expr = _TIME_GRAIN_SQL[grain].format(col=grain_col)
        select = f"SELECT {grain_expr} AS bucket, {agg_expr} AS value"
        group_by = " GROUP BY bucket"
    else:
        select = f"SELECT {agg_expr} AS value"
        group_by = ""

    # FROM clause
    from_clause = f" FROM {entity}"

    # JOIN clauses
    join_sql = ""
    for j in joins:
        left = j["on"]["left"]
        right = j["on"]["right"]
        join_sql += f" {j['type'].upper()} JOIN {j['table']} ON {left} = {right}"

    # WHERE clause
    where_parts = []
    for f in filters:
        col = f["column"]
        op = f["op"]
        if f["op"] == "is_null":
            where_parts.append(f"{col} IS NULL")
        elif f["op"] == "is_not_null":
            where_parts.append(f"{col} IS NOT NULL")
        elif f["op"] == "in":
            placeholders.extend(f["value"])
            placeholders_for_in = ",".join(["%s"] * len(f["value"]))
            where_parts.append(f"{col} IN ({placeholders_for_in})")
        elif f["op"] == "not_in":
            placeholders.extend(f["value"])
            placeholders_for_in = ",".join(["%s"] * len(f["value"]))
            where_parts.append(f"{col} NOT IN ({placeholders_for_in})")
        else:
            placeholders.append(f["value"])
            where_parts.append(f"{col} {op} %s")

    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    sql = select + from_clause + join_sql + where_sql + group_by
    return sql
