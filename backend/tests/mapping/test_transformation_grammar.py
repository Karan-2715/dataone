"""Unit tests for the restricted transformation grammar."""
import pytest

from app.services.transformation_grammar import (
    GrammarError, compile_sql, parse,
)


def test_direct_accepts_empty_payload():
    ast = parse({"kind": "direct"})
    assert ast["kind"] == "direct"


def test_unknown_kind_rejected():
    with pytest.raises(GrammarError) as e:
        parse({"kind": "evil_eval"})
    assert e.value.kind == "unknown_kind"


def test_payload_must_be_object():
    with pytest.raises(GrammarError):
        parse("not an object")
    with pytest.raises(GrammarError):
        parse(42)


def test_cast_requires_from_to():
    with pytest.raises(GrammarError) as e1:
        parse({"kind": "cast", "from": "TEXT"})
    assert e1.value.kind == "missing_field"
    with pytest.raises(GrammarError):
        parse({"kind": "cast", "from": 1, "to": "TEXT"})
    ast = parse({"kind": "cast", "from": "TEXT", "to": "VARCHAR"})
    assert ast["payload"]["to"] == "VARCHAR"


def test_concat_rejects_empty_or_non_list():
    for bad in [{}, {"kind": "concat", "parts": []}, {"kind": "concat", "parts": "x"}]:
        with pytest.raises(GrammarError):
            parse(bad)


def test_concat_validates_part_kinds():
    with pytest.raises(GrammarError):
        parse({"kind": "concat", "parts": [{"kind": "lambda", "value": "x"}]})
    with pytest.raises(GrammarError):
        parse({"kind": "concat", "parts": [{"kind": "literal", "value": 1}]})


def test_substring_out_of_range():
    with pytest.raises(GrammarError):
        compile_sql(
            {"kind": "substring", "source_index": 5, "start": 0, "length": 3},
            ["a", "b"], [],
        )


def test_substring_compiles_with_index():
    placeholders = []
    frag = compile_sql(
        {"kind": "substring", "source_index": 1, "start": 0, "length": 3},
        ["a", "b"], placeholders,
    )
    assert "SUBSTRING" in frag


def test_coalesce_compiles_with_literal_placeholder():
    placeholders = []
    frag = compile_sql(
        {"kind": "coalesce", "fallback_kind": "literal", "fallback_value": "n/a"},
        ["src"], placeholders,
    )
    assert frag == "COALESCE(%s, %s)"
    assert placeholders == ["n/a"]


def test_default_compiles():
    placeholders = []
    frag = compile_sql(
        {"kind": "default", "value_kind": "literal", "value": "X"},
        ["src"], placeholders,
    )
    assert frag == "COALESCE(%s, %s)"
    assert placeholders == ["X"]


def test_null_if_compiles():
    placeholders = []
    frag = compile_sql({"kind": "null_if", "equals": ""}, ["src"], placeholders)
    assert frag == "NULLIF(%s, %s)"
    assert placeholders == [""]


def test_lookup_with_optional_default():
    placeholders = []
    frag = compile_sql(
        {"kind": "lookup", "table": "lu_country",
         "key_column": "code", "value_column": "name"},
        ["src"], placeholders,
    )
    assert "SELECT name FROM lu_country" in frag
    assert "code = %s" in frag
    assert placeholders == []


def test_lookup_with_default_appends_placeholder():
    placeholders = []
    frag = compile_sql(
        {"kind": "lookup", "table": "lu_country",
         "key_column": "code", "value_column": "name", "default": "UNK"},
        ["src"], placeholders,
    )
    assert placeholders == ["UNK"]
    assert frag.count("%s") == 2


def test_upper_lower_trim_compile():
    for kind in ("upper", "lower", "trim"):
        frag = compile_sql({"kind": kind}, ["src"], [])
        assert "%s" in frag


def test_validate_is_idempotent():
    parse({"kind": "cast", "from": "INT", "to": "TEXT"})
    # second call must not raise
    parse({"kind": "cast", "from": "INT", "to": "TEXT"})


def test_concat_too_many_source_parts():
    with pytest.raises(GrammarError) as e:
        compile_sql(
            {"kind": "concat", "parts": [
                {"kind": "source"}, {"kind": "source"}, {"kind": "source"},
            ]},
            ["a"], [],
        )
    assert "concat" in e.value.location


def test_all_twelve_kinds_parse():
    kinds = [
        {"kind": "direct"},
        {"kind": "cast", "from": "TEXT", "to": "VARCHAR"},
        {"kind": "concat", "parts": [{"kind": "literal", "value": "x"}]},
        {"kind": "substring", "source_index": 0, "start": 0, "length": 5},
        {"kind": "coalesce", "fallback_kind": "literal", "fallback_value": "x"},
        {"kind": "upper"},
        {"kind": "lower"},
        {"kind": "trim"},
        {"kind": "default", "value_kind": "literal", "value": "x"},
        {"kind": "null_if", "equals": ""},
        {"kind": "lookup", "table": "t", "key_column": "k", "value_column": "v"},
        {"kind": "case", "operator": ">", "compare_value": 0, "then_value": "a", "else_value": "b"},
    ]
    for k in kinds:
        ast = parse(k)
        assert ast["kind"] == k["kind"]


# ── case (threshold conditional) ─────────────────────────────────────────
# annual_revenue > 500_000_000 -> "Enterprise" else "SMB"; employee_count
# > 0 -> 1 else 0 — the two use cases this kind was built for.


def test_case_requires_all_four_fields():
    base = {"kind": "case", "operator": ">", "compare_value": 0, "then_value": 1, "else_value": 0}
    for missing in ("operator", "compare_value", "then_value", "else_value"):
        payload = {k: v for k, v in base.items() if k != missing}
        with pytest.raises(GrammarError) as e:
            parse(payload)
        assert e.value.kind == "missing_field"


def test_case_rejects_unknown_operator():
    with pytest.raises(GrammarError) as e:
        parse({"kind": "case", "operator": "=>", "compare_value": 0, "then_value": 1, "else_value": 0})
    assert e.value.kind == "bad_type"
    assert e.value.location == "case.operator"


@pytest.mark.parametrize("op,sql_op", [
    (">", ">"), (">=", ">="), ("<", "<"), ("<=", "<="), ("==", "="), ("!=", "<>"),
])
def test_case_compiles_every_operator(op, sql_op):
    placeholders = []
    frag = compile_sql(
        {"kind": "case", "operator": op, "compare_value": 500_000_000,
         "then_value": "Enterprise", "else_value": "SMB"},
        ["annual_revenue"], placeholders,
    )
    assert frag == f"CASE WHEN %s {sql_op} %s THEN %s ELSE %s END"
    assert placeholders == [500_000_000, "Enterprise", "SMB"]


def test_case_supports_numeric_then_else_values():
    # employee_count > 0 -> 1 else 0
    placeholders = []
    frag = compile_sql(
        {"kind": "case", "operator": ">", "compare_value": 0, "then_value": 1, "else_value": 0},
        ["employee_count"], placeholders,
    )
    assert frag == "CASE WHEN %s > %s THEN %s ELSE %s END"
    assert placeholders == [0, 1, 0]


def test_case_requires_at_least_one_source_column():
    with pytest.raises(GrammarError):
        compile_sql(
            {"kind": "case", "operator": ">", "compare_value": 0, "then_value": 1, "else_value": 0},
            [], [],
        )


# ── SQL injection surface (review §11.3) ─────────────────────────────────
# The grammar promises `docs/mapper-mapping-contract.md`: "No string
# interpolation of user data into SQL." The compile_sql output for `cast`
# and `lookup` embeds `payload["to"]` / `payload["table"]` / etc. into the
# fragment. Every value that flows there must therefore be either a known
# SQL type name (cast.to) or a valid SQL identifier (lookup.*).

import pytest


def test_cast_to_rejects_sql_injection():
    with pytest.raises(GrammarError) as e:
        parse({"kind": "cast", "from": "TEXT", "to": "TEXT); DROP TABLE users; --"})
    assert e.value.kind == "bad_type"
    assert e.value.location == "cast.to"


def test_cast_to_rejects_dotted_identifier():
    # Not a type name; would be "CAST(x AS users.id)" which is invalid SQL.
    with pytest.raises(GrammarError):
        parse({"kind": "cast", "from": "TEXT", "to": "users.id"})


def test_cast_to_rejects_unknown_type():
    # NUMERIC(10,2) and BYTEA are real Postgres types but not in the
    # supported SQL_TYPES set; users must use one of the supported names.
    with pytest.raises(GrammarError):
        parse({"kind": "cast", "from": "TEXT", "to": "NUMERIC(10,2)"})
    with pytest.raises(GrammarError):
        parse({"kind": "cast", "from": "TEXT", "to": "BYTEA"})


def test_cast_to_accepts_every_supported_sql_type():
    from app.services.transformation_grammar import SQL_TYPES
    for t in sorted(SQL_TYPES):
        # Each must parse without raising.
        ast = parse({"kind": "cast", "from": "TEXT", "to": t})
        assert ast["payload"]["to"] == t


def test_lookup_table_rejects_sql_injection():
    with pytest.raises(GrammarError) as e:
        parse({
            "kind": "lookup", "table": "users; DELETE FROM users; --",
            "key_column": "id", "value_column": "name",
        })
    assert e.value.kind == "bad_type"
    assert e.value.location == "lookup.table"


def test_lookup_key_column_rejects_injection():
    with pytest.raises(GrammarError) as e:
        parse({
            "kind": "lookup", "table": "lu_country",
            "key_column": "id'; DROP TABLE users; --", "value_column": "name",
        })
    assert e.value.location == "lookup.key_column"


def test_lookup_value_column_rejects_injection():
    with pytest.raises(GrammarError) as e:
        parse({
            "kind": "lookup", "table": "lu_country",
            "key_column": "code", "value_column": "1 OR 1=1",
        })
    assert e.value.location == "lookup.value_column"


def test_lookup_rejects_identifier_starting_with_digit():
    with pytest.raises(GrammarError):
        parse({
            "kind": "lookup", "table": "lu_country",
            "key_column": "123starts_with_digit", "value_column": "name",
        })


def test_lookup_rejects_identifier_with_spaces():
    with pytest.raises(GrammarError):
        parse({
            "kind": "lookup", "table": "lu country",
            "key_column": "code", "value_column": "name",
        })


def test_lookup_accepts_valid_identifiers():
    # The full set of legal identifier characters: letters, digits (not
    # first), underscore. Including underscores and mixed-case.
    ast = parse({
        "kind": "lookup",
        "table": "lu_country_codes",
        "key_column": "country_code_2",
        "value_column": "Country_Name",
    })
    assert ast["payload"]["table"] == "lu_country_codes"
    assert ast["payload"]["key_column"] == "country_code_2"
    assert ast["payload"]["value_column"] == "Country_Name"


def test_lookup_default_still_accepts_arbitrary_string():
    # `default` is a literal value bound as a query parameter, NOT
    # interpolated into the SQL fragment; it can be any string.
    ast = parse({
        "kind": "lookup", "table": "lu_country",
        "key_column": "code", "value_column": "name",
        "default": "anything goes here; even with spaces",
    })
    assert ast["payload"]["default"] == "anything goes here; even with spaces"
