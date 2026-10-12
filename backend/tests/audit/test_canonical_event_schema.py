"""Tests for the canonical audit event schema + SDK contract (AUDIT-T1).

Covers the "Verify" checklist from
requirements-specs/audit_trail_tasks/01_canonical_event_schema_contract.md:
  - emit_audit_event creates an AuditLog row with all canonical fields.
  - correlation_id is returned and can be used for tracing.
  - record_audit (legacy helper) still works for backward compatibility.
"""
from __future__ import annotations

from app.models.audit import AuditLog
from app.schemas.audit import AuditEventResponse
from app.services.audit_helper import emit_audit_event, record_audit


def test_emit_audit_event_persists_all_canonical_fields(db, admin):
    correlation_id = emit_audit_event(
        db,
        event_type="connector.created",
        actor=admin.email,
        module="connectors",
        target_type="connection",
        target_id=42,
        target_name="prod-postgres",
        before=None,
        after={"host": "db.internal"},
        outcome="success",
        summary="Created connection prod-postgres",
        duration_ms=120,
        metadata={"sql": "N/A", "row_count": 0},
    )
    db.commit()

    row = db.query(AuditLog).filter(AuditLog.correlation_id == correlation_id).one()
    assert row.event_type == "connector.created"
    assert row.actor == admin.email
    assert row.module == "connectors"
    assert row.target_type == "connection"
    assert row.target_id == "42"  # stored as string for flexibility
    assert row.target_name == "prod-postgres"
    assert row.after_summary == {"host": "db.internal"}
    assert row.outcome == "success"
    assert row.summary == "Created connection prod-postgres"
    assert row.duration_ms == 120
    assert row.event_metadata == {"sql": "N/A", "row_count": 0}


def test_correlation_id_generated_when_not_provided_and_usable_for_tracing(db, admin):
    id_a = emit_audit_event(db, event_type="pipeline.started", actor=admin.email, module="pipelines")
    id_b = emit_audit_event(
        db, event_type="pipeline.completed", actor=admin.email, module="pipelines",
        correlation_id=id_a,
    )
    db.commit()

    assert id_a  # auto-generated, non-empty
    assert id_b == id_a  # explicit correlation_id round-trips

    trace = (
        db.query(AuditLog)
        .filter(AuditLog.correlation_id == id_a)
        .order_by(AuditLog.id.asc())
        .all()
    )
    assert [e.event_type for e in trace] == ["pipeline.started", "pipeline.completed"]


def test_record_audit_legacy_helper_still_works(db):
    record_audit(
        db,
        event_type="connector.tested",
        actor="admin",
        connection_id=None,
        connection_name="legacy-conn",
        payload={"note": "legacy call site"},
        status="success",
    )
    db.commit()

    row = db.query(AuditLog).filter(AuditLog.event_type == "connector.tested").one()
    assert row.module == "legacy"
    assert row.connection_name == "legacy-conn"
    assert row.payload == {"note": "legacy call site"}
    assert row.outcome == "success"
    assert row.status == "success"


def test_audit_event_response_serializes_metadata_field(db, admin):
    """Regression test: `metadata` is reserved on SQLAlchemy's declarative Base,
    so the ORM attribute is `event_metadata` while the API/response field stays
    `metadata`. AuditEventResponse must alias across that gap when built via
    model_validate(orm_instance).
    """
    emit_audit_event(
        db, event_type="query.executed", actor=admin.email, module="query_studio",
        metadata={"sql": "SELECT 1", "row_count": 1},
    )
    db.commit()

    row = db.query(AuditLog).filter(AuditLog.event_type == "query.executed").one()
    response = AuditEventResponse.model_validate(row)
    assert response.metadata == {"sql": "SELECT 1", "row_count": 1}
