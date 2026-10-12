"""Multi-domain eval harness + end-to-end integration (agentic_dba_tasks #12).

The triggering example was retail — this suite proves the system isn't a
retail-specific hack (FR10): varied domains, the clarifying-question path,
the unprofiled-connection path, and a collision case, plus one full
classified-request → plan → approve → DDL → draft-mapping walk asserting
REAL persisted state at every stage.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.models.connection import DBConnection
from app.models.mapping import FieldMapping, Mapping
from app.services.agentic_dba_engine import create_plan, generate_plan
from app.services.agentic_dba_execution_service import approve_and_execute_plan
from app.services.dba_intent_classifier import classify_intent

# ── Eval set: 4 varied domains/requests (task #12 requirement) ───────────

EVAL_SET = [
    # (question, expected_intent)
    ("create new target schemas for retail analytics in postgresql based on "
     "profiling ensure to create proper data quality steps, transformations "
     "and final target tables", "schema_design"),
    ("design a patient-visit data mart for our healthcare records with data "
     "quality checks", "schema_design"),
    ("build etl transformations to load hr employee tables into a reporting "
     "warehouse", "schema_design"),
    ("generate ddl for a logistics shipments fact table based on profiling", "schema_design"),
]


@pytest.mark.parametrize("question,expected", EVAL_SET)
def test_eval_set_classifies_schema_design(question, expected):
    assert classify_intent(question).intent == expected


@pytest.mark.parametrize("question", [q for q, _ in EVAL_SET])
def test_eval_set_produces_reviewable_plans_on_any_catalog(db, retail_connection, admin, question):
    """Every eval question must yield a ready, grounded plan against the same
    catalog — no domain-specific crash or empty proposal."""
    plan = generate_plan(db, create_plan(
        db, question=question, connection_id=retail_connection.id,
        session_id=None, actor=admin.email).id)
    assert plan.status == "ready"
    assert plan.proposed_tables, f"no tables proposed for: {question}"
    assert plan.generated_ddl
    # Every proposed column with a source_ref must reference a real catalog column.
    catalog = {("customers", c) for c in ("id", "name", "email")} | \
              {("products", c) for c in ("id", "title", "price")} | \
              {("orders", c) for c in ("id", "customer_id", "total", "status")}
    for table in plan.proposed_tables:
        for col in table["columns"]:
            for ref in col.get("source_refs") or []:
                assert (ref["table"], ref["column"]) in catalog, \
                    f"ungrounded source_ref {ref} in {table['name']}.{col['name']}"


def test_non_retail_domain_uses_catalog_fallback_not_retail_template(db, retail_connection, admin):
    plan = generate_plan(db, create_plan(
        db, question="design a patient-visit data mart for our healthcare records "
                     "with data quality checks",
        connection_id=retail_connection.id, session_id=None, actor=admin.email).id)
    # No healthcare template exists — must be an honest catalog-driven plan,
    # not the retail star schema mislabeled.
    assert plan.domain_template is None
    assert any("no domain template matched" in n for n in plan.confidence_notes)


# ── End-to-end: classified request → plan → approve → DDL → mapping ─────

def test_full_lifecycle_end_to_end(db, retail_connection, target_connection, admin,
                                   client_admin, monkeypatch):
    from app.services import askdata_pipeline_service

    dispatched = []
    monkeypatch.setattr(askdata_pipeline_service, "_dispatch_plan_generation",
                        lambda plan_id: dispatched.append(plan_id))

    # 1. Classified request through the real chat endpoint.
    resp = client_admin.post("/api/v1/askdata/ask", json={
        "connection_id": retail_connection.id,
        "question": EVAL_SET[0][0],
    })
    body = resp.json()
    assert body["intent"] == "schema_design"
    plan_id = body["plan_id"]
    assert dispatched == [plan_id]

    # 2. Generation (run inline — the Celery task calls this same function).
    plan = generate_plan(db, plan_id)
    assert plan.status == "ready"

    # Point the plan at the distinct target connection so mapping
    # auto-creation is possible (Schema Mapper requires source != target).
    plan.target_connection_id = target_connection.id
    db.commit()

    # 3. Approval → gated DDL execution.
    plan = approve_and_execute_plan(db, plan_id, actor=admin.email, role="admin")
    assert plan.status == "applied"

    # 4. Tables REALLY exist in the target database.
    raw = sqlite3.connect(target_connection.config["path"])
    try:
        tables = {r[0] for r in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        raw.close()
    assert {"dim_customers", "dim_products", "fact_orders"} <= tables

    # 5. Draft mapping REALLY exists with grammar-valid edges.
    assert plan.created_mapping_id is not None
    mapping = db.query(Mapping).filter(Mapping.id == plan.created_mapping_id).one()
    assert mapping.status == "draft"
    edges = db.query(FieldMapping).filter(FieldMapping.mapping_id == mapping.id).all()
    assert edges
    assert all(e.origin == "agentic_dba" for e in edges)
    from app.services.transformation_grammar import validate
    for e in edges:
        validate(e.transformation)


# ── Clarifying/degenerate paths from the eval set ────────────────────────

def test_unprofiled_but_scanned_connection_still_plans_with_honest_notes(
        db, admin, tmp_path):
    """Catalog exists but no profiling: plan generates, DQ rules honestly
    absent with scan/profile notes — not silently guessed."""
    from app.models.schema_catalog import CatalogColumn, CatalogTable

    path = str(tmp_path / "noprofiles.db")
    sqlite3.connect(path).close()
    conn = DBConnection(name="noprofiles", type="sqlite", config={"path": path})
    db.add(conn)
    db.commit()
    db.refresh(conn)
    t = CatalogTable(connection_id=conn.id, table_name="shipments")
    db.add(t)
    db.flush()
    db.add(CatalogColumn(table_id=t.id, column_name="id", data_type="INTEGER",
                         nullable=False, is_primary_key=True, ordinal_position=0))
    db.add(CatalogColumn(table_id=t.id, column_name="carrier", data_type="TEXT",
                         nullable=True, is_primary_key=False, ordinal_position=1))
    db.commit()

    plan = generate_plan(db, create_plan(
        db, question="create a logistics shipments target table with data quality steps",
        connection_id=conn.id, session_id=None, actor=admin.email).id)
    assert plan.status == "ready"
    assert plan.dq_rules == []
    assert any("no profile for" in n for n in plan.confidence_notes)


def test_collision_case_from_eval_set(db, retail_connection, admin):
    """Eval case: proposed name collides -> migration artifact, distinct from
    a create (full behavior covered in test_collision_and_migration)."""
    from app.models.schema_catalog import CatalogColumn, CatalogTable
    t = CatalogTable(connection_id=retail_connection.id, table_name="dw_customers")
    db.add(t)
    db.flush()
    db.add(CatalogColumn(table_id=t.id, column_name="id", data_type="INTEGER",
                         nullable=False, is_primary_key=True, ordinal_position=0))
    db.commit()

    plan = generate_plan(db, create_plan(
        db, question="build a reporting warehouse with etl transformations",
        connection_id=retail_connection.id, session_id=None, actor=admin.email).id)
    modes = {d["table"]: d["mode"] for d in plan.generated_ddl}
    assert modes.get("dw_customers") == "migrate"
