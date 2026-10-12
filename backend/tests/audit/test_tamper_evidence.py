"""Tests for append-only store + tamper-evidence (AUDIT-T3).

Covers the task's own "Verify" checklist:
  - hash chain: insert N events, verify chain is valid
  - tamper detection: a corrupted row is caught by verify_hash_chain
  - API-level append-only: POST succeeds, PUT/DELETE return 405
  - DB-level append-only: direct UPDATE/DELETE are rejected by the trigger
  - verification endpoint reports correct chain health
"""
from __future__ import annotations

from sqlalchemy import text

from app.models.audit import AuditLog
from app.services.audit_helper import emit_audit_event, verify_hash_chain


def test_hash_chain_valid_after_several_inserts(db, admin):
    for i in range(3):
        emit_audit_event(db, event_type=f"event.{i}", actor=admin.email)
        db.commit()

    result = verify_hash_chain(db)
    assert result["valid"] is True
    assert result["total_events"] == 3
    assert result["verified_events"] == 3
    assert result["chain_broken_at"] is None

    rows = db.query(AuditLog).order_by(AuditLog.id.asc()).all()
    assert [r.sequence for r in rows] == [1, 2, 3]
    assert rows[0].prev_hash is None
    assert rows[1].prev_hash == rows[0].event_hash
    assert rows[2].prev_hash == rows[1].event_hash


def test_verify_endpoint_reports_chain_health(client, db, admin):
    emit_audit_event(db, event_type="a", actor=admin.email)
    emit_audit_event(db, event_type="b", actor=admin.email)
    db.commit()

    resp = client.post("/api/v1/audit/verify")
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["total_events"] == 2
    assert body["tampered_events"] == []


def test_tamper_detection_catches_direct_row_corruption(db, admin, engine):
    emit_audit_event(db, event_type="a", actor=admin.email)
    emit_audit_event(db, event_type="b", actor=admin.email)
    emit_audit_event(db, event_type="c", actor=admin.email)
    db.commit()

    tampered_id = db.query(AuditLog).filter(AuditLog.event_type == "b").one().id

    # Simulate an attacker with raw DB access bypassing the app entirely —
    # the append-only trigger is defense-in-depth, not a guarantee against a
    # superuser with DDL rights, so a real tamper attempt would first drop
    # or disable it (exactly why the hash chain is the layer that actually
    # has to catch this).
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER audit_log_no_update"))
        conn.execute(
            text("UPDATE audit_log SET actor = 'attacker' WHERE id = :id"),
            {"id": tampered_id},
        )

    result = verify_hash_chain(db)
    assert result["valid"] is False
    assert tampered_id in result["tampered_events"]


def test_db_level_append_only_blocks_update_and_delete(db, admin, engine):
    emit_audit_event(db, event_type="a", actor=admin.email)
    db.commit()
    row_id = db.query(AuditLog).filter(AuditLog.event_type == "a").one().id

    with engine.connect() as conn:
        try:
            conn.execute(text("UPDATE audit_log SET actor='z' WHERE id=:id"), {"id": row_id})
            conn.commit()
            assert False, "UPDATE should have been rejected by the append-only trigger"
        except Exception as exc:
            assert "append-only" in str(exc)

    with engine.connect() as conn:
        try:
            conn.execute(text("DELETE FROM audit_log WHERE id=:id"), {"id": row_id})
            conn.commit()
            assert False, "DELETE should have been rejected by the append-only trigger"
        except Exception as exc:
            assert "append-only" in str(exc)

    # Row is untouched.
    row = db.query(AuditLog).filter(AuditLog.id == row_id).one()
    assert row.actor == admin.email


def test_api_level_append_only_put_delete_not_allowed(client, db, admin):
    emit_audit_event(db, event_type="a", actor=admin.email)
    db.commit()
    row_id = db.query(AuditLog).filter(AuditLog.event_type == "a").one().id

    assert client.put(f"/api/v1/audit/events/{row_id}", json={}).status_code == 405
    assert client.delete(f"/api/v1/audit/events/{row_id}").status_code == 405
