"""Planning engine tests (agentic_dba_tasks #3): template match,
catalog-only fallback, sanity caps, missing-catalog handling."""
from __future__ import annotations

import pytest

from app.services.agentic_dba_engine import create_plan, generate_plan

RETAIL_QUESTION = (
    "create new target schemas for retail analytics in postgresql based on "
    "profiling ensure to create proper data quality steps, transformations "
    "and final target tables"
)


def test_retail_question_matches_template_and_grounds_in_catalog(db, retail_connection, admin):
    plan = create_plan(db, question=RETAIL_QUESTION, connection_id=retail_connection.id,
                       session_id=None, actor=admin.email)
    assert plan.status == "generating"
    plan = generate_plan(db, plan.id)

    assert plan.status == "ready"
    assert plan.domain_template == "retail_analytics"
    names = {t["name"] for t in plan.proposed_tables}
    assert {"dim_customers", "dim_products", "fact_orders"} <= names

    dim_customers = next(t for t in plan.proposed_tables if t["name"] == "dim_customers")
    col_names = [c["name"] for c in dim_customers["columns"]]
    assert col_names[0] == "customer_key"  # surrogate key first
    assert "email" in col_names           # grounded in the real catalog
    email = next(c for c in dim_customers["columns"] if c["name"] == "email")
    assert email["source_refs"] == [{"table": "customers", "column": "email", "type": "TEXT"}]


def test_generated_ddl_is_create_mode_for_new_tables(db, retail_connection, admin):
    plan = generate_plan(db, create_plan(
        db, question=RETAIL_QUESTION, connection_id=retail_connection.id,
        session_id=None, actor=admin.email).id)
    ddl_by_table = {d["table"]: d for d in plan.generated_ddl}
    assert ddl_by_table["dim_customers"]["mode"] == "create"
    assert ddl_by_table["dim_customers"]["statements"][0].startswith("CREATE TABLE dim_customers")
    assert "customer_key INTEGER PRIMARY KEY" in ddl_by_table["dim_customers"]["statements"][0]


def test_non_template_question_falls_back_to_catalog_driven(db, retail_connection, admin):
    plan = generate_plan(db, create_plan(
        db, question="create target tables for the finance reporting mart",
        connection_id=retail_connection.id, session_id=None, actor=admin.email).id)
    assert plan.status == "ready"
    assert plan.domain_template is None
    names = {t["name"] for t in plan.proposed_tables}
    assert names == {"dw_customers", "dw_products", "dw_orders"}
    assert any("no domain template matched" in n for n in plan.confidence_notes)


def test_sanity_cap_truncates_with_explicit_note(db, retail_connection, admin, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "AGENTIC_DBA_MAX_TABLES", 1)
    plan = generate_plan(db, create_plan(
        db, question="create target tables for the finance reporting mart",
        connection_id=retail_connection.id, session_id=None, actor=admin.email).id)
    assert len(plan.proposed_tables) == 1
    assert any("capped at 1 tables" in n for n in plan.confidence_notes)


def test_unscanned_connection_fails_with_scan_first_error(db, admin, tmp_path):
    import sqlite3
    from app.models.connection import DBConnection
    path = str(tmp_path / "unscanned.db")
    sqlite3.connect(path).close()
    conn = DBConnection(name="unscanned", type="sqlite", config={"path": path})
    db.add(conn)
    db.commit()
    db.refresh(conn)

    plan = generate_plan(db, create_plan(
        db, question=RETAIL_QUESTION, connection_id=conn.id,
        session_id=None, actor=admin.email).id)
    assert plan.status == "failed"
    assert "scan" in plan.error.lower()


def test_unknown_connection_raises(db, admin):
    with pytest.raises(ValueError):
        create_plan(db, question=RETAIL_QUESTION, connection_id=999999,
                    session_id=None, actor=admin.email)


def test_llm_unavailable_is_an_honest_note_not_a_failure(db, retail_connection, admin, monkeypatch):
    """LLM enabled but unreachable — generation must still succeed
    deterministically with an explicit note, never a hard failure."""
    import requests as requests_module
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENTIC_DBA_LLM_ENABLED", True)

    def _refuse(*args, **kwargs):
        raise requests_module.ConnectionError("connection refused")

    monkeypatch.setattr("app.services.agentic_dba_engine.requests.post", _refuse)

    plan = generate_plan(db, create_plan(
        db, question=RETAIL_QUESTION, connection_id=retail_connection.id,
        session_id=None, actor=admin.email).id)
    assert plan.status == "ready"
    assert plan.proposed_tables  # deterministic proposal stands
    assert any("LLM" in n or "Ollama" in n for n in plan.confidence_notes)


def test_llm_ungrounded_output_is_rejected(db, retail_connection, admin, monkeypatch):
    """An LLM adaptation referencing a column that doesn't exist in the
    catalog must be rejected wholesale — the deterministic proposal stands."""
    import json as json_module

    from app.core.config import settings
    monkeypatch.setattr(settings, "AGENTIC_DBA_LLM_ENABLED", True)

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"response": json_module.dumps([{
                "name": "dim_hallucinated",
                "columns": [{"name": "ghost", "type": "TEXT", "nullable": True,
                             "primary_key": False,
                             "source_refs": [{"table": "no_such_table", "column": "ghost"}]}],
            }])}

    monkeypatch.setattr("app.services.agentic_dba_engine.requests.post",
                        lambda *a, **k: _Resp())

    plan = generate_plan(db, create_plan(
        db, question=RETAIL_QUESTION, connection_id=retail_connection.id,
        session_id=None, actor=admin.email).id)
    assert plan.status == "ready"
    names = {t["name"] for t in plan.proposed_tables}
    assert "dim_hallucinated" not in names
    assert "dim_customers" in names  # deterministic template proposal kept
    assert any("grounding/shape validation" in n for n in plan.confidence_notes)


def test_llm_unsafe_type_is_rejected(db, retail_connection, admin, monkeypatch):
    """Regression (v3 bugs2 #2): an LLM adaptation whose column `type` isn't a
    plain SQL type token must be rejected wholesale (grounding validation),
    not passed verbatim into generated DDL."""
    import json as json_module

    from app.core.config import settings
    monkeypatch.setattr(settings, "AGENTIC_DBA_LLM_ENABLED", True)

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"response": json_module.dumps([{
                "name": "dim_customers",
                "columns": [{"name": "email", "type": "TEXT); DROP TABLE users; --",
                             "nullable": True, "primary_key": False,
                             "source_refs": [{"table": "customers", "column": "email"}]}],
            }])}

    monkeypatch.setattr("app.services.agentic_dba_engine.requests.post",
                        lambda *a, **k: _Resp())

    plan = generate_plan(db, create_plan(
        db, question=RETAIL_QUESTION, connection_id=retail_connection.id,
        session_id=None, actor=admin.email).id)
    assert plan.status == "ready"
    assert any("grounding/shape validation" in n for n in plan.confidence_notes)
    # No generated DDL statement carries the injected fragment.
    all_sql = " ".join(s for obj in (plan.generated_ddl or [])
                       for s in obj.get("statements", []))
    assert "DROP TABLE users" not in all_sql


def test_plan_api_flow(client_admin, db, retail_connection, monkeypatch):
    """POST /plan -> 202 generating; GET /plans/{id} reflects readiness."""
    from app.api.routers import agentic_dba as router_module
    from app.services.agentic_dba_engine import generate_plan as real_generate

    generated = []
    monkeypatch.setattr(router_module, "dispatch_plan_generation",
                        lambda plan_id: generated.append(plan_id))

    resp = client_admin.post("/api/v1/agentic-dba/plan", json={
        "connection_id": retail_connection.id, "question": RETAIL_QUESTION,
    })
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "generating"
    assert generated == [body["plan_id"]]

    real_generate(db, body["plan_id"])
    detail = client_admin.get(f"/api/v1/agentic-dba/plans/{body['plan_id']}").json()
    assert detail["status"] == "ready"
    assert detail["proposed_tables"]
    assert detail["generated_ddl"]
