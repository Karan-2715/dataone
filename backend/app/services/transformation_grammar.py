"""Restricted, allow-listed transformation grammar for schema mappings.

No freeform DSL. Input is a structured JSON dict with a ``kind`` field; each
allowed kind has a fixed payload shape. This module rejects everything else.

Each AST node exposes ``compile_sql(dialect, placeholders)`` which returns a
parameterized SQL fragment and appends any literal placeholders to the
``placeholders`` list (caller binds them).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Tuple


ALLOWED_KINDS: frozenset = frozenset({
    "direct", "cast", "concat", "substring", "coalesce",
    "upper", "lower", "trim", "default", "null_if", "lookup", "case",
})

# `case`'s allowed comparison operators, mapped to their SQL spelling.
# `==`/`!=` (not `=`/`<>`) match the rest of this app's JSON-facing
# conventions (JS/Python equality operators) — the SQL form is an
# implementation detail of _sql_case, never user-facing.
_COMPARISON_SQL: Dict[str, str] = {
    ">": ">", ">=": ">=", "<": "<", "<=": "<=", "==": "=", "!=": "<>",
}

# Transformation kinds that accept >1 source column. All others emit a
# single positional placeholder (`%s` for the source value), so attaching
# 2+ sources to a non-MULTI_SOURCE_KIND edge would produce a parameter-
# count mismatch at execution time. The service layer enforces this
# invariant at add_edge / update_edge_transformation time
# (mapper_tasks #1). `concat` is the only kind whose _sql_concat()
# iterates sources explicitly.
MULTI_SOURCE_KINDS: frozenset = frozenset({"concat"})

# Review §11.3: restrict identifiers and type names that flow into SQL.
# Any field tagged "identifier" must match this regex; any field tagged
# "sql_type" must be a member of SQL_TYPES below.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# SQL type names recognized by the validation service (mirrors the type
# families in app/services/mapping_validation_service.py). Used by the
# "sql_type" field-type tag for `cast.to` — keeps users from interpolating
# arbitrary identifiers into a CAST(... AS <user-string>) fragment.
SQL_TYPES: frozenset = frozenset({
    # Text
    "TEXT", "VARCHAR", "CHAR", "CLOB", "STRING",
    # Integer
    "INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT",
    # Float
    "FLOAT", "DOUBLE", "REAL", "DECIMAL", "NUMERIC",
    # Date / time
    "DATE", "TIMESTAMP", "DATETIME", "TIMESTAMPTZ",
    # Boolean
    "BOOLEAN", "BOOL",
})


class GrammarError(ValueError):
    """Raised when a transformation payload is invalid.

    Attributes:
        kind: short error category ("unknown_kind", "missing_field", "bad_type")
        location: dotted path within the payload, e.g. "concat.parts[0].value"
    """

    def __init__(self, message: str, *, kind: str = "grammar_error", location: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.location = location

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "message": str(self), "location": self.location}


# Per-kind schema: field-name -> (type-tag, required)
# Type tags:
#   "str"              — any non-empty string (free text; NEVER interpolated into SQL)
#   "int"              — integer
#   "bool"             — boolean
#   "any"              — pass-through (type-checked at use site if needed)
#   "list_concat_parts"— structured concat-parts array
#   "identifier"       — SQL identifier (review §11.3); must match _IDENT_RE
#   "sql_type"         — SQL type name; must be in SQL_TYPES
#   "comparison_op"    — one of _COMPARISON_SQL's keys
_KIND_SCHEMAS: Dict[str, Dict[str, Tuple[str, bool]]] = {
    "direct": {},
    "cast": {"from": ("str", True), "to": ("sql_type", True)},
    "concat": {"parts": ("list_concat_parts", True)},
    "substring": {"source_index": ("int", True), "start": ("int", True), "length": ("int", True)},
    "coalesce": {"fallback_kind": ("str", True), "fallback_value": ("any", True)},
    "upper": {},
    "lower": {},
    "trim": {},
    "default": {"value_kind": ("str", True), "value": ("any", True)},
    "null_if": {"equals": ("any", True)},
    "lookup": {"table": ("identifier", True), "key_column": ("identifier", True),
               "value_column": ("identifier", True), "default": ("any", False)},
    # Single-condition threshold conditional: IF source <op> compare_value
    # THEN then_value ELSE else_value. E.g. annual_revenue > 500000000 ->
    # "Enterprise" else "SMB". Deliberately one condition, not a WHEN-chain —
    # the two use cases this was built for (both binary thresholds) don't
    # need more, and a chain is a natural, separate future extension rather
    # than something to build speculatively now.
    "case": {
        "operator": ("comparison_op", True),
        "compare_value": ("any", True),
        "then_value": ("any", True),
        "else_value": ("any", True),
    },
}


def parse(payload: Any) -> Dict[str, Any]:
    """Validate a transformation payload and return a normalized AST dict."""
    if not isinstance(payload, dict):
        raise GrammarError("transformation must be an object", kind="bad_type", location="$")
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in ALLOWED_KINDS:
        raise GrammarError(
            f"unknown transformation kind '{kind}'; allowed: {sorted(ALLOWED_KINDS)}",
            kind="unknown_kind",
            location="kind",
        )
    schema = _KIND_SCHEMAS[kind]
    body: Dict[str, Any] = {}
    for fname, (ftype, required) in schema.items():
        if fname not in payload:
            if required:
                raise GrammarError(
                    f"missing required field '{fname}' for kind '{kind}'",
                    kind="missing_field",
                    location=fname,
                )
            continue
        value = payload[fname]
        body[fname] = _check_field(value, ftype, f"{kind}.{fname}")
    return {"kind": kind, "payload": body, "_sql_fn": _SQL_FNS[kind]}


def _check_field(value: Any, ftype: str, location: str) -> Any:
    if ftype == "str":
        if not isinstance(value, str):
            raise GrammarError(f"expected string at {location}", kind="bad_type", location=location)
        return value
    if ftype == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise GrammarError(f"expected integer at {location}", kind="bad_type", location=location)
        return value
    if ftype == "bool":
        if not isinstance(value, bool):
            raise GrammarError(f"expected boolean at {location}", kind="bad_type", location=location)
        return value
    if ftype == "identifier":
        # Review §11.3: reject anything that isn't a plain SQL identifier.
        # This is the guard that prevents `table = "users; DROP TABLE x; --"`
        # from being interpolated into compile_sql output.
        if not isinstance(value, str) or not _IDENT_RE.fullmatch(value):
            raise GrammarError(
                f"expected a valid SQL identifier at {location} (got {value!r})",
                kind="bad_type",
                location=location,
            )
        return value
    if ftype == "sql_type":
        # `cast.to` must be a recognized SQL type name; arbitrary strings
        # (e.g. "TEXT); DROP TABLE --") are rejected here rather than
        # being passed to the SQL compiler.
        if not isinstance(value, str) or value.upper() not in SQL_TYPES:
            raise GrammarError(
                f"expected a known SQL type at {location} (got {value!r}); "
                f"allowed: {sorted(SQL_TYPES)}",
                kind="bad_type",
                location=location,
            )
        return value
    if ftype == "any":
        return value
    if ftype == "comparison_op":
        if not isinstance(value, str) or value not in _COMPARISON_SQL:
            raise GrammarError(
                f"expected a comparison operator at {location} (got {value!r}); "
                f"allowed: {sorted(_COMPARISON_SQL)}",
                kind="bad_type",
                location=location,
            )
        return value
    if ftype == "list_concat_parts":
        if not isinstance(value, list) or not value:
            raise GrammarError(
                f"expected non-empty list at {location}",
                kind="bad_type",
                location=location,
            )
        for i, part in enumerate(value):
            if not isinstance(part, dict):
                raise GrammarError(
                    f"concat.parts[{i}] must be an object",
                    kind="bad_type",
                    location=f"{location}[{i}]",
                )
            pk = part.get("kind")
            if pk == "literal":
                if "value" not in part or not isinstance(part["value"], str):
                    raise GrammarError(
                        f"concat.parts[{i}].value must be string",
                        kind="bad_type",
                        location=f"{location}[{i}].value",
                    )
            elif pk == "source":
                pass  # N:1 sources are referenced by index at compile time
            else:
                raise GrammarError(
                    f"concat.parts[{i}].kind must be 'literal' or 'source'",
                    kind="bad_type",
                    location=f"{location}[{i}].kind",
                )
        return value
    raise GrammarError(
        f"internal: unknown field type '{ftype}'",
        kind="internal",
        location=location,
    )


# SQL fragment builders ---------------------------------------------------
# Each function returns a SQL fragment that uses positional %s placeholders.
# Source-column references count as placeholders the caller must bind, in
# source-list order. Literal placeholders are appended to ``placeholders``.


def _sql_direct(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    if not sources:
        raise GrammarError("direct requires at least one source column",
                           kind="bad_type", location="sources")
    return "%s"


def _sql_cast(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    if not sources:
        raise GrammarError("cast requires at least one source column",
                           kind="bad_type", location="sources")
    return f"CAST(%s AS {payload['to']})"


def _sql_concat(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    parts = payload["parts"]
    frags: List[str] = []
    src_iter = iter(range(len(sources)))
    for part in parts:
        if part["kind"] == "literal":
            placeholders.append(part["value"])
            frags.append("%s")
        else:
            try:
                next(src_iter)
            except StopIteration:
                raise GrammarError(
                    "concat has more 'source' parts than sources provided",
                    kind="bad_type",
                    location="concat.parts",
                )
            frags.append("%s")
    return " || ".join(frags) if frags else "''"


def _sql_substring(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    idx = payload["source_index"]
    if idx < 0 or idx >= len(sources):
        raise GrammarError(
            f"substring.source_index {idx} out of range (have {len(sources)} sources)",
            kind="bad_type",
            location="substring.source_index",
        )
    return f"SUBSTRING(%s, {int(payload['start']) + 1}, {int(payload['length'])})"


def _sql_coalesce(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    if not sources:
        raise GrammarError("coalesce requires at least one source column",
                           kind="bad_type", location="sources")
    placeholders.append(payload["fallback_value"])
    return "COALESCE(%s, %s)"


def _sql_upper(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    if not sources:
        raise GrammarError("upper requires at least one source column",
                           kind="bad_type", location="sources")
    return "UPPER(%s)"


def _sql_lower(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    if not sources:
        raise GrammarError("lower requires at least one source column",
                           kind="bad_type", location="sources")
    return "LOWER(%s)"


def _sql_trim(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    if not sources:
        raise GrammarError("trim requires at least one source column",
                           kind="bad_type", location="sources")
    return "TRIM(%s)"


def _sql_default(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    if not sources:
        raise GrammarError("default requires at least one source column",
                           kind="bad_type", location="sources")
    placeholders.append(payload["value"])
    return "COALESCE(%s, %s)"


def _sql_null_if(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    if not sources:
        raise GrammarError("null_if requires at least one source column",
                           kind="bad_type", location="sources")
    placeholders.append(payload["equals"])
    return "NULLIF(%s, %s)"


def _sql_lookup(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    if not sources:
        raise GrammarError("lookup requires at least one source column",
                           kind="bad_type", location="sources")
    tbl = payload["table"]
    kc = payload["key_column"]
    vc = payload["value_column"]
    default_clause = ""
    if "default" in payload and payload["default"] is not None:
        placeholders.append(payload["default"])
        default_clause = ", %s"
    return f"(SELECT {vc} FROM {tbl} WHERE {kc} = %s{default_clause})"


def _sql_case(payload: Dict[str, Any], sources: List[str], placeholders: List[Any]) -> str:
    if not sources:
        raise GrammarError("case requires at least one source column",
                           kind="bad_type", location="sources")
    op = _COMPARISON_SQL[payload["operator"]]
    placeholders.append(payload["compare_value"])
    placeholders.append(payload["then_value"])
    placeholders.append(payload["else_value"])
    return f"CASE WHEN %s {op} %s THEN %s ELSE %s END"


_SQL_FNS: Dict[str, Callable] = {
    "direct": _sql_direct,
    "cast": _sql_cast,
    "concat": _sql_concat,
    "substring": _sql_substring,
    "coalesce": _sql_coalesce,
    "upper": _sql_upper,
    "lower": _sql_lower,
    "trim": _sql_trim,
    "default": _sql_default,
    "null_if": _sql_null_if,
    "lookup": _sql_lookup,
    "case": _sql_case,
}


def compile_sql(transformation: Dict[str, Any], sources: List[str],
                placeholders: List[Any]) -> str:
    """Render a transformation into a parameterized SQL fragment."""
    ast = parse(transformation)
    return ast["_sql_fn"](ast["payload"], sources, placeholders)


def validate(transformation: Dict[str, Any]) -> None:
    """Re-validate a transformation payload (defense in depth)."""
    parse(transformation)
