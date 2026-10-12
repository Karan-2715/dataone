"""Intent registry extensibility + clarifying-question tests
(agentic_dba_tasks #10)."""
from __future__ import annotations

import pytest

from app.services.dba_intent_classifier import (
    IntentSpec,
    classify_intent,
    register_intent,
    registered_intents,
    unregister_intent,
)

RETAIL_EXAMPLE = (
    "create new target schemas for retail analytics in postgresql based on "
    "profiling ensure to create proper data quality steps, transformations "
    "and final target tables"
)


# ── Registry extensibility ───────────────────────────────────────────────

@pytest.fixture()
def dummy_intent():
    """Register a second custom intent, prove dispatch, clean up."""
    def _match_index_proposal(text):
        if "slow query" in text.lower() and "index" in text.lower():
            return 5.0, 0.9, "index-proposal keywords"
        return None

    spec = IntentSpec(name="index_proposal", matcher=_match_index_proposal,
                      handler="index_advisor", priority=20)
    register_intent(spec)
    try:
        yield spec
    finally:
        unregister_intent("index_proposal")


def test_registering_new_intent_dispatches_without_touching_core(dummy_intent):
    c = classify_intent("propose an index for this slow query on orders")
    assert c.intent == "index_proposal"
    assert c.handler == "index_advisor"
    assert c.confidence == 0.9


def test_builtin_intents_unaffected_by_registration(dummy_intent):
    assert classify_intent(RETAIL_EXAMPLE).intent == "schema_design"
    assert classify_intent("show me all customers").intent == "read_query"


def test_registry_lists_intents_by_priority():
    names = [s.name for s in registered_intents()]
    assert names.index("schema_design") < names.index("read_query")


def test_classification_carries_handler_routing_hint():
    assert classify_intent(RETAIL_EXAMPLE).handler == "agentic_dba_engine"
    assert classify_intent("count rows in orders").handler == "nl2sql"


# ── Clarifying-question flow ─────────────────────────────────────────────

def test_design_request_on_unscanned_connection_asks_to_scan_first(
        client_analyst, sqlite_conn_unscanned):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_unscanned.id,
        "question": RETAIL_EXAMPLE,
    })
    body = resp.json()
    assert body["intent"] == "schema_design"
    assert body["needs_clarification"] is True
    assert body["plan_id"] is None
    assert "scan" in body["summary"].lower()
    assert body["sql"] is None


def test_ambiguous_question_without_table_reference_asks_for_clarification(
        client_analyst, sqlite_conn_scanned):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "do the thing",
    })
    body = resp.json()
    assert body["intent"] == "ambiguous"
    assert body["needs_clarification"] is True
    assert body["executed"] is False


def test_ambiguous_question_naming_a_table_proceeds_as_read_query(
        client_analyst, sqlite_conn_scanned):
    """A bare table name is a reasonable read-query guess — today's behavior
    is preserved rather than nagging the user."""
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "customers",
    })
    body = resp.json()
    assert body["intent"] == "ambiguous"
    assert body["needs_clarification"] is False
    assert body["executed"] is True


def test_schema_design_on_scanned_connection_spawns_plan(
        client_analyst, sqlite_conn_scanned, db, monkeypatch):
    from app.services import askdata_pipeline_service

    dispatched = []
    monkeypatch.setattr(askdata_pipeline_service, "_dispatch_plan_generation",
                        lambda plan_id: dispatched.append(plan_id))

    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": RETAIL_EXAMPLE,
    })
    body = resp.json()
    assert body["intent"] == "schema_design"
    assert body["plan_id"] is not None
    assert dispatched == [body["plan_id"]]

    from app.models.schema_design_plan import SchemaDesignPlan
    plan = db.query(SchemaDesignPlan).filter(
        SchemaDesignPlan.id == body["plan_id"]).one()
    assert plan.status == "generating"
    assert plan.session_id == body["session_id"]
    assert plan.question == RETAIL_EXAMPLE
