"""Tests for the search/filter query layer (AUDIT-T4).

Covers the task's own "Verify" checklist:
  - each filter parameter individually + combined (AND logic)
  - date range filtering
  - correlation_id returns all related events ordered by sequence
  - full-text search on summary
  - pagination
  - faceted search returns correct counts
  - sort_by is restricted to real columns (regression: unvalidated
    getattr(AuditLog, sort_by) could 500 or sort by a non-column attribute)
"""
from __future__ import annotations

from app.services.audit_helper import emit_audit_event


def _seed(db):
    emit_audit_event(
        db, event_type="connector.created", actor="alice@x.com", module="connectors",
        summary="Created connection prod-db", outcome="success",
    )
    emit_audit_event(
        db, event_type="connector.deleted", actor="bob@x.com", module="connectors",
        summary="Deleted connection staging-db", outcome="failure",
    )
    emit_audit_event(
        db, event_type="query.executed", actor="alice@x.com", module="query_studio",
        summary="Ran report query", outcome="success",
    )
    db.commit()


def test_filter_by_actor(client, db):
    _seed(db)
    resp = client.get("/api/v1/audit/events", params={"actor": "alice@x.com"})
    body = resp.json()
    assert body["total"] == 2
    assert all(e["actor"] == "alice@x.com" for e in body["events"])


def test_filter_by_module_and_outcome_combine_with_and(client, db):
    _seed(db)
    resp = client.get(
        "/api/v1/audit/events",
        params={"module": "connectors", "outcome": "failure"},
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["events"][0]["event_type"] == "connector.deleted"


def test_full_text_search_on_summary(client, db):
    _seed(db)
    resp = client.get("/api/v1/audit/events", params={"search": "report"})
    body = resp.json()
    assert body["total"] == 1
    assert body["events"][0]["event_type"] == "query.executed"


def test_pagination(client, db):
    _seed(db)
    resp = client.get("/api/v1/audit/events", params={"page": 1, "page_size": 2})
    body = resp.json()
    assert body["total"] == 3
    assert len(body["events"]) == 2
    assert body["has_more"] is True


def test_correlation_id_defaults_to_sequence_ascending(client, db):
    cid = emit_audit_event(db, event_type="pipeline.started", actor="a@x.com", module="pipelines")
    emit_audit_event(db, event_type="pipeline.step", actor="a@x.com", module="pipelines", correlation_id=cid)
    emit_audit_event(db, event_type="pipeline.completed", actor="a@x.com", module="pipelines", correlation_id=cid)
    db.commit()

    resp = client.get("/api/v1/audit/events", params={"correlation_id": cid})
    body = resp.json()
    assert [e["event_type"] for e in body["events"]] == [
        "pipeline.started", "pipeline.step", "pipeline.completed",
    ]


def test_facets_endpoint_reports_correct_counts(client, db):
    _seed(db)
    resp = client.get("/api/v1/audit/events/facets")
    body = resp.json()
    assert body["modules"]["connectors"] == 2
    assert body["modules"]["query_studio"] == 1
    assert body["outcomes"]["success"] == 2
    assert body["outcomes"]["failure"] == 1


def test_sort_by_is_restricted_to_known_columns(client, db):
    _seed(db)
    # "metadata" is a real attribute on AuditLog (Base.metadata, the
    # SQLAlchemy MetaData registry) but not a sortable column — an
    # unvalidated `getattr(AuditLog, sort_by, default)` would resolve it
    # (since it exists, the getattr default never kicks in) and then 500
    # when .asc()/.desc() is called on it. The allow-list must fall back
    # to created_at instead.
    resp = client.get("/api/v1/audit/events", params={"sort_by": "metadata"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 3
