"""Tests for the AskData intent classification gate (agentic_dba_tasks #1).

The retail-analytics example is the literal user report that triggered this
epic — it MUST classify schema_design and MUST NOT reach NL2SQL generation.
The read-query cases mirror the existing AskData fixtures to prove no
regression on the path that already worked.
"""
from __future__ import annotations

import pytest

from app.models.audit import AuditLog
from app.services.dba_intent_classifier import classify_intent

RETAIL_EXAMPLE = (
    "create new target schemas for retail analytics in postgresql based on "
    "profiling ensure to create proper data quality steps, transformations "
    "and final target tables"
)


# ── Unit: classifier buckets ─────────────────────────────────────────────

def test_retail_analytics_example_classifies_schema_design():
    c = classify_intent(RETAIL_EXAMPLE)
    assert c.intent == "schema_design"
    assert c.confidence >= 0.6
    assert "create" in c.matched_signal


@pytest.mark.parametrize("question", [
    "show me all customers from New York",
    "count rows",
    "how many employees are in each department?",
    "what's the total revenue by product category?",
    "show everything in customers",
    "list tables",
])
def test_read_queries_stay_read_query(question):
    assert classify_intent(question).intent == "read_query"


@pytest.mark.parametrize("question", [
    "design a star schema for the sales warehouse",
    "build etl transformations into final target tables",
    "generate ddl for a new orders table",
    "set up a data quality check pipeline for customer emails",
])
def test_build_requests_classify_schema_design(question):
    assert classify_intent(question).intent == "schema_design"


@pytest.mark.parametrize("question", [
    "show me orders created last week",   # past tense ≠ build verb
    "which user created this table?",     # 'created' + question word
    "list all tables",                    # build noun without build verb
])
def test_read_questions_containing_build_words_stay_read_query(question):
    assert classify_intent(question).intent == "read_query"


def test_no_signal_is_ambiguous():
    c = classify_intent("customers")
    assert c.intent == "ambiguous"


def test_empty_question_is_ambiguous():
    assert classify_intent("").intent == "ambiguous"
    assert classify_intent("   ").intent == "ambiguous"


# ── Integration: gate short-circuits before generation ──────────────────

def test_schema_design_never_reaches_nl2sql(client_analyst, sqlite_conn_scanned, monkeypatch):
    from app.services.nl2sql_service import NL2SQLService

    def _boom(*args, **kwargs):
        raise AssertionError("generate_sql must not be called for schema_design intent")

    monkeypatch.setattr(NL2SQLService, "generate_sql", staticmethod(_boom))

    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": RETAIL_EXAMPLE,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "schema_design"
    assert body["sql"] is None
    assert body["executed"] is False
    assert body["error"] is None
    assert "design" in body["summary"].lower()
    assert "approval" in body["summary"].lower()


def test_read_query_path_unchanged(client_analyst, sqlite_conn_scanned):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "show everything in customers",
    })
    body = resp.json()
    assert body["intent"] == "read_query"
    assert body["executed"] is True
    assert body["row_count"] == 3


def test_intent_classification_audited(client_analyst, sqlite_conn_scanned, db):
    client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": RETAIL_EXAMPLE,
    })
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "askdata.intent_classified")
        .one()
    )
    assert row.module == "askdata"
    assert row.event_metadata["intent"] == "schema_design"
    assert row.event_metadata["matched_signal"]
