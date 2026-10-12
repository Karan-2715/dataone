"""external_action intent tests (aci_integration_tasks #4): routed to ACI
tool discovery + the governed approval queue — never NL2SQL, never ungated
execution; ambiguous targets get a clarifying question."""
from __future__ import annotations

import pytest

from app.models.audit import AuditLog
from app.models.autopilot import AutopilotRecommendation
from app.services.dba_intent_classifier import classify_intent


class _FakeAci:
    def __init__(self):
        self.searched = []

    def search_tools(self, query, limit=5):
        self.searched.append(query)
        return [{"name": "SLACK__CHAT_POST_MESSAGE"}]


@pytest.fixture()
def fake_aci_search(monkeypatch):
    fake = _FakeAci()
    monkeypatch.setattr("app.services.aci_client_service.aci_client.search_tools",
                        fake.search_tools)
    return fake


# ── Classification ───────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "post this table's PII findings to the #data-governance Slack channel",
    "email the data quality report to ops@example.com",
    "open a github issue for this schema drift",
    "create a jira ticket for the failed pipeline",
])
def test_external_requests_classify_external_action(question):
    c = classify_intent(question)
    assert c.intent == "external_action"
    assert c.handler == "aci_client_service"


@pytest.mark.parametrize("question", [
    "show me all customers from New York",
    "create new target schemas for retail analytics based on profiling with target tables",
    "how many employees are in each department?",
])
def test_non_external_requests_unaffected(question):
    assert classify_intent(question).intent != "external_action"


@pytest.mark.parametrize("question", [
    # Regression (v3 bugs2 #3): a schema-design request that merely names a
    # SaaS as its data domain must NOT be misrouted to the ACI approval queue.
    "create target tables for our jira ticketing data",
    "design a star schema for our github issues warehouse",
])
def test_schema_design_naming_a_saas_domain_is_not_external(question):
    assert classify_intent(question).intent == "schema_design"


# ── Routing through the chat endpoint ────────────────────────────────────

def test_external_action_never_reaches_nl2sql(client_analyst, sqlite_conn_scanned,
                                              fake_aci_search, monkeypatch, db):
    from app.services.nl2sql_service import NL2SQLService

    def _boom(*args, **kwargs):
        raise AssertionError("generate_sql must not be called for external_action")

    monkeypatch.setattr(NL2SQLService, "generate_sql", staticmethod(_boom))

    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "post this table's PII findings to the #data-governance Slack channel",
    })
    body = resp.json()
    assert body["intent"] == "external_action"
    assert body["sql"] is None
    assert body["executed"] is False
    assert body["recommendation_id"] is not None
    assert "approval" in body["summary"].lower()
    assert fake_aci_search.searched  # tool discovery ran


def test_external_action_queues_approval_only_recommendation(
        client_analyst, sqlite_conn_scanned, fake_aci_search, db):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "email the data quality report to ops@example.com",
    })
    body = resp.json()
    rec = (
        db.query(AutopilotRecommendation)
        .filter(AutopilotRecommendation.id == body["recommendation_id"])
        .one()
    )
    assert rec.action_type == "external_email_send"
    assert rec.status == "pending"          # queued, NOT executed
    assert rec.payload["to"] == "ops@example.com"
    assert rec.risk == "high"


def test_channel_request_maps_to_message_send(client_analyst, sqlite_conn_scanned,
                                              fake_aci_search, db):
    client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "post the drift summary to #data-governance",
    })
    rec = db.query(AutopilotRecommendation).one()
    assert rec.action_type == "external_message_send"
    assert rec.payload["destination"] == "#data-governance"


def test_ticket_request_maps_to_ticket_create(client_analyst, sqlite_conn_scanned,
                                              fake_aci_search, db):
    client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "open a github issue for this schema drift",
    })
    rec = db.query(AutopilotRecommendation).one()
    assert rec.action_type == "external_ticket_create"


def test_unresolvable_target_asks_for_clarification(client_analyst, sqlite_conn_scanned,
                                                    fake_aci_search, db):
    resp = client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "email this report to the team",  # no address, no channel, no ticket words
    })
    body = resp.json()
    assert body["intent"] == "external_action"
    assert body["needs_clarification"] is True
    assert body["recommendation_id"] is None
    assert db.query(AutopilotRecommendation).count() == 0
    assert fake_aci_search.searched == []  # no discovery before a target exists


# ── Target resolution precedence (v4 bugs2 #2) ───────────────────────────

@pytest.mark.parametrize("question,expected", [
    # An explicit ticket request wins over an incidental cc'd email address...
    ("open a Jira ticket for the outage, cc bob@corp.com", "external_ticket_create"),
    # ...and over an issue/PR number that would look like a #channel.
    ("open a GitHub issue for bug #500", "external_ticket_create"),
    # A bare email with no ticket phrasing still routes to email.
    ("email the report to ops@example.com", "external_email_send"),
    # An incidental ticketing word without a creation verb stays email.
    ("email bob@x.com about the issue", "external_email_send"),
    # A real #channel (letter-led) still routes to a message.
    ("post the drift summary to #data-governance", "external_message_send"),
])
def test_external_target_resolution_precedence(question, expected):
    from app.services.askdata_pipeline_service import _resolve_external_target
    target = _resolve_external_target(question)
    assert target is not None
    assert target["action_type"] == expected


def test_bare_issue_number_is_not_a_channel():
    from app.services.askdata_pipeline_service import _resolve_external_target
    # No ticket verb, no email, no letter-led channel — "#500" must not be
    # mistaken for a Slack channel; there's simply no resolvable target.
    assert _resolve_external_target("send an update about #500") is None


def test_external_action_request_audited(client_analyst, sqlite_conn_scanned,
                                         fake_aci_search, db):
    client_analyst.post("/api/v1/askdata/ask", json={
        "connection_id": sqlite_conn_scanned.id,
        "question": "create a jira ticket for the failed pipeline",
    })
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "aci.external_action_requested")
        .one()
    )
    assert row.module == "aci_integration"
    assert row.event_metadata["action_type"] == "external_ticket_create"
    assert row.event_metadata["recommendation_id"] is not None
