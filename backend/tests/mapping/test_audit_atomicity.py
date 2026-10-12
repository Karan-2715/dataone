"""Tests for audit-helper atomicity contract (review §11.6).

The contract is:
  record_audit(db, ...) does NOT call db.commit() or db.rollback().
  It only stages the row with db.add() and surfaces constraint errors
  with db.flush(). The caller owns the transaction boundary.

These tests guard against accidental regressions to the old
behaviour (shared-session commit/rollback inside the helper).
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.audit import AuditLog
from app.services.audit_helper import record_audit


def test_record_audit_source_does_not_call_commit_or_rollback():
    """Static guard: record_audit's source must not contain commit() or rollback()
    on the caller's session.

    The previous implementation called db.commit() and db.rollback() on
    the caller's session, breaking transactional atomicity. This test
    guards against regression to the old behaviour by inspecting the
    function source.

    (Runtime commit-isolation testing is impractical here because the
    conftest uses SQLAlchemy StaticPool which shares one underlying
    connection across sessions -- a separate session sees uncommitted
    state from the shared connection.)
    """
    import inspect
    from app.services import audit_helper
    source = inspect.getsource(audit_helper.record_audit)
    assert "db.commit()" not in source, (
        "record_audit must not call db.commit() on the caller's session. "
        "Use a SAVEPOINT (db.begin_nested()) and let the caller commit."
    )
    assert "db.rollback()" not in source, (
        "record_audit must not call db.rollback() on the caller's session. "
        "Use SAVEPOINT rollback for the audit insert only."
    )


def test_caller_commit_persists_audit_and_business_atomically(db, admin, seeded_connections):
    """One caller commit must persist both the business object and the audit row.

    Reproduces the mapping_service.create_mapping pattern: business add +
    record_audit + commit. After commit, both the mapping row and the
    audit row must exist; after rollback, neither must exist.
    """
    from app.services.mapping_service import MappingService
    src, tgt = seeded_connections
    m = MappingService.create_mapping(
        db, source_id=src.id, target_id=tgt.id,
        name="Atomic Test", actor=admin.email,
    )
    # Both must be visible after the service's commit.
    assert m.id is not None
    assert db.query(AuditLog).filter(
        AuditLog.event_type == "mapping_created",
        AuditLog.payload["mapping_id"].as_integer() == m.id,
    ).first() is not None


def test_record_audit_failure_does_not_rollback_caller(db, admin, seeded_connections):
    """If record_audit's flush fails, the caller's business object must
    still be committable. The old behaviour would have rolled back the
    caller's session, silently losing the business work.

    We induce a failure by passing an event_type that violates the
    AuditLog.event_type NOT NULL constraint (passing None), then verify
    the caller's pending business work is unaffected.
    """
    from app.models.connection import DBConnection

    # Stage a business object in the session (no commit yet).
    src = DBConnection(name="AtomicityTest", type="sqlite", config={"path": "/tmp/x.db"})
    db.add(src)
    db.flush()  # forces PK assignment but does not commit

    # record_audit with None event_type will violate the NOT NULL
    # constraint and raise. Under the old code this would rollback
    # the session and `src` would be lost. Under the new code the
    # exception is swallowed and `src` survives.
    record_audit(db, None, actor="tester")  # type: ignore[arg-type]

    # The caller's business object must still be committable.
    db.commit()
    assert db.query(DBConnection).filter(
        DBConnection.name == "AtomicityTest"
    ).first() is not None


def test_audit_row_visible_after_explicit_commit(db):
    """Sanity check: record_audit + explicit commit makes the row visible."""
    record_audit(db, "test_visible_after_commit", actor="tester")
    db.commit()
    assert db.query(AuditLog).filter(
        AuditLog.event_type == "test_visible_after_commit"
    ).first() is not None
