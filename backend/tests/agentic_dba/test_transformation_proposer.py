"""Transformation proposal tests (agentic_dba_tasks #5) — kind selection per
source/target pair, N:1 concat, null-handling coalesce, honest can't-express."""
from __future__ import annotations

from app.services.transformation_grammar import validate
from app.services.transformation_proposer import propose_transformations


def _table(name, columns):
    return [{"name": name, "columns": columns}]


def _col(name, type_, source_refs, nullable=True):
    return {"name": name, "type": type_, "nullable": nullable,
            "primary_key": False, "source_refs": source_refs}


def test_same_type_proposes_direct(db, retail_connection):
    out, _ = propose_transformations(db, retail_connection.id, _table("dim_customers", [
        _col("name", "TEXT", [{"table": "customers", "column": "name", "type": "TEXT"}]),
    ]))
    assert out[0]["transformation"] == {"kind": "direct"}


def test_cross_family_type_proposes_cast(db, retail_connection):
    out, _ = propose_transformations(db, retail_connection.id, _table("fact_orders", [
        _col("total_text", "VARCHAR", [{"table": "orders", "column": "total", "type": "REAL"}]),
    ]))
    t = out[0]["transformation"]
    assert t["kind"] == "cast"
    assert t["to"] == "VARCHAR"
    validate(t)  # emitted payload must be grammar-valid


def test_multi_source_proposes_concat_with_separator(db, retail_connection):
    out, _ = propose_transformations(db, retail_connection.id, _table("dim_customers", [
        _col("full_contact", "TEXT", [
            {"table": "customers", "column": "name", "type": "TEXT"},
            {"table": "customers", "column": "email", "type": "TEXT"},
        ]),
    ]))
    t = out[0]["transformation"]
    assert t["kind"] == "concat"
    kinds = [p["kind"] for p in t["parts"]]
    assert kinds == ["source", "literal", "source"]  # literal " " separator
    validate(t)


def test_nullable_source_to_not_null_target_proposes_coalesce(db, retail_connection, monkeypatch):
    # customers.email profile has null_rate 0.0 — synthesize a null-y profile
    from app.models.schema_catalog import CatalogColumn, CatalogTable, ColumnProfile
    profile = (
        db.query(ColumnProfile)
        .join(CatalogColumn, ColumnProfile.column_id == CatalogColumn.id)
        .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
        .filter(CatalogTable.table_name == "customers",
                CatalogColumn.column_name == "email")
        .one()
    )
    profile.null_rate = 0.12
    db.commit()

    out, _ = propose_transformations(db, retail_connection.id, _table("dim_customers", [
        _col("email", "TEXT",
             [{"table": "customers", "column": "email", "type": "TEXT"}],
             nullable=False),
    ]))
    t = out[0]["transformation"]
    assert t["kind"] == "coalesce"
    assert "review the fallback value" in out[0]["note"]
    validate(t)


def test_inexpressible_type_change_left_unset_with_note(db, retail_connection):
    out, notes = propose_transformations(db, retail_connection.id, _table("dim_x", [
        _col("payload", "GEOMETRY", [{"table": "customers", "column": "name", "type": "TEXT"}]),
    ]))
    assert out[0]["transformation"] is None
    assert "author manually" in out[0]["note"]
    assert notes


def test_columns_without_sources_produce_no_entry(db, retail_connection):
    out, _ = propose_transformations(db, retail_connection.id, _table("dim_x", [
        {"name": "surrogate_key", "type": "INTEGER", "nullable": False,
         "primary_key": True, "source_refs": []},
    ]))
    assert out == []
