"""Tests for the platform_insight intent (Enterprise v2, E09 — Unified
AI Copilot). The copilot must answer questions about the platform's own
risk/DQ/governance metadata by grounding in E05/E06/E08's real services,
never by attempting NL2SQL against the connected source database."""
from __future__ import annotations

import pytest

from app.models.schema_catalog import CatalogColumn, CatalogTable, ColumnClassification
from app.services.dba_intent_classifier import classify_intent


# ── Unit: classifier ─────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "what are the critical risks right now?",
    "show me the top risks",
    "what's my data quality score?",
    "what is our governance score?",
    "what's the governance coverage?",
    "what's our compliance status?",
    # build-validation B-E09-10 — natural-language phrasings the original
    # (narrower) phrase set missed.
    "what are my risks?",
    "how is my data quality?",
    "what's the governance status?",
    "are there any compliance issues?",
])
def test_platform_metric_phrases_classify_as_platform_insight(question):
    c = classify_intent(question)
    assert c.intent == "platform_insight"
    assert c.handler == "platform_insight_service"
    assert c.confidence >= 0.75


@pytest.mark.parametrize("question", [
    "show me all rows in the risks table",
    "select * from quality_checks",
    "how many rows does the governance table have",
])
def test_bare_platform_nouns_without_the_specific_phrase_do_not_misfire(question):
    # Deliberately narrow: only specific multi-word phrases trigger this
    # intent, so a real table/column merely named "risks" or "governance"
    # is not hijacked into the platform-insight branch.
    assert classify_intent(question).intent != "platform_insight"


def test_platform_insight_outranks_schema_design_on_a_mixed_question():
    # "create" + "table" genuinely triggers schema_design's matcher too —
    # this proves platform_insight's higher score/priority actually wins
    # the arbitration, not just that schema_design failed to fire at all.
    c = classify_intent("create a table showing our critical risks")
    assert c.intent == "platform_insight"


# ── Router: GET /api/v1/askdata/ask dispatch ─────────────────────────────

def test_critical_risks_question_grounds_in_real_risk_register(
    client_analyst, sqlite_conn_scanned, db,
):
    table = CatalogTable(connection_id=sqlite_conn_scanned.id, table_name="users")
    db.add(table)
    db.flush()
    col = CatalogColumn(table_id=table.id, column_name="email", data_type="TEXT")
    db.add(col)
    db.flush()
    db.add(ColumnClassification(column_id=col.id, label="PII", level="High",
                                confidence=0.9, method="value_pattern"))
    db.commit()

    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "what are the critical risks?",
    })
    body = resp.json()
    assert body["intent"] == "platform_insight"
    assert body["sql"] is None
    assert body["executed"] is False
    assert body["platform_insight"]["risk"]["total"] >= 1
    assert "critical" in body["summary"].lower()


def test_dq_score_question_grounds_in_real_scorecard(client_analyst, sqlite_conn_scanned):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "what's my data quality score?",
    })
    body = resp.json()
    assert body["intent"] == "platform_insight"
    assert "data_quality" in body["platform_insight"]
    assert body["sql"] is None


def test_governance_score_question_grounds_in_real_score(client_analyst, sqlite_conn_scanned):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "what is our governance score?",
    })
    body = resp.json()
    assert body["intent"] == "platform_insight"
    assert "governance" in body["platform_insight"]
    # build-validation B-E09-11: a governance-only question must not also
    # trigger the risk register — the most expensive adapter (live
    # connector round-trips).
    assert "risk" not in body["platform_insight"]


def test_dq_only_question_does_not_load_the_risk_register(client_analyst, sqlite_conn_scanned):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "what's my data quality score?",
    })
    body = resp.json()
    assert "data_quality" in body["platform_insight"]
    assert "risk" not in body["platform_insight"]


def test_platform_insight_skips_grounding_even_on_unscanned_connection(
    client_analyst, sqlite_conn_unscanned,
):
    """Unlike schema_design, platform_insight never needs the connection's
    catalog — it must not ask the user to scan first."""
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_unscanned.id,
        "question": "what are the critical risks?",
    })
    body = resp.json()
    assert body["intent"] == "platform_insight"
    assert body["needs_clarification"] is False
    assert body["grounded"] is False  # honest: no catalog grounding was even attempted
