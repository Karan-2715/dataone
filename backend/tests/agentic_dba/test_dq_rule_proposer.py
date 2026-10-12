"""DQ rule proposal tests (agentic_dba_tasks #4) — every rule type against
the profiled retail fixture, plus the no-profile no-guess case."""
from __future__ import annotations

from app.services.dq_rule_proposer import propose_dq_rules


def _proposed(table, cols):
    """Minimal proposed-table shape: every column sourced 1:1."""
    return [{
        "name": table,
        "columns": [
            {"name": c, "type": "TEXT", "nullable": True, "primary_key": False,
             "source_refs": [{"table": src_t, "column": src_c, "type": "TEXT"}]}
            for c, (src_t, src_c) in cols.items()
        ],
    }]


def test_not_null_and_unique_from_clean_pk_profile(db, retail_connection):
    rules, notes = propose_dq_rules(db, retail_connection.id, _proposed(
        "dim_customers", {"customer_id": ("customers", "id")}))
    kinds = {r["rule"] for r in rules}
    assert "not_null" in kinds
    assert "unique" in kinds
    unique = next(r for r in rules if r["rule"] == "unique")
    assert "sample" in unique["justification"]          # never asserted as certain
    assert "guarantee" in unique["justification"]
    not_null = next(r for r in rules if r["rule"] == "not_null")
    assert "0.00%" in not_null["justification"]         # cites the exact rate


def test_fk_candidate_becomes_inferred_fk_rule(db, retail_connection):
    rules, _ = propose_dq_rules(db, retail_connection.id, _proposed(
        "fact_orders", {"customer_id": ("orders", "customer_id")}))
    fk = next(r for r in rules if r["rule"] == "foreign_key")
    assert fk["references"] == {"table": "customers", "column": "id"}
    assert "inferred, not verified" in fk["justification"]
    assert fk["confidence"] == 0.95


def test_duplicates_propose_dedupe_step_not_constraint(db, retail_connection):
    rules, _ = propose_dq_rules(db, retail_connection.id, _proposed(
        "dim_customers", {"email": ("customers", "email")}))
    kinds = {r["rule"] for r in rules}
    assert "dedupe" in kinds
    assert "unique" not in kinds  # 0.95 uniqueness is below the 0.99 bar
    dedupe = next(r for r in rules if r["rule"] == "dedupe")
    assert "ahead of load" in dedupe["justification"]


def test_no_profile_means_no_rule_and_an_explicit_note(db, retail_connection):
    rules, notes = propose_dq_rules(db, retail_connection.id, _proposed(
        "dim_products", {"title": ("products", "title")}))  # products.title has no profile
    assert rules == []
    assert any("no profile for products.title" in n for n in notes)


def test_columns_without_source_refs_are_skipped(db, retail_connection):
    rules, notes = propose_dq_rules(db, retail_connection.id, [{
        "name": "dim_x",
        "columns": [{"name": "surrogate_key", "type": "INTEGER",
                     "nullable": False, "primary_key": True, "source_refs": []}],
    }])
    assert rules == []
    assert notes == []
