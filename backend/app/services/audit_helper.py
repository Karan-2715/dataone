"""Shared helper for writing audit log entries.

Contract (review §11.6):
    emit_audit_event() does NOT call db.commit() or db.rollback() on the
    caller's session. It stages the row inside a SAVEPOINT so that an
    audit-write failure rolls back only the audit insert, never the
    caller's pending business work. The CALLER owns the outer
    transaction boundary.

    Using a SAVEPOINT via db.begin_nested() means:
      - Successful audit insert: the row is staged in the caller's
        session; the caller's commit() persists it atomically with the
        business work.
      - Failed audit insert: the SAVEPOINT is rolled back; the caller's
        session remains valid (no connection-invalidated error); the
        caller's subsequent commit() proceeds with just the business
        work.

    ``emit_audit_event`` never raises — every other service in the app
    calls it inline with business logic and must not have its own
    transaction broken by an audit-write failure.

    ``ingest_audit_event_durable`` (AUDIT-T2) is the one exception: it's
    used only by the dedicated ``POST /audit/events`` ingestion endpoint,
    which has no competing business transaction to protect, and DOES raise
    (``AuditBufferFullError``) when a write fails and the durable fallback
    buffer (app.core.audit_buffer) is also full — the endpoint turns that
    into backpressure (503) rather than silently losing the event.

Hash chain (AUDIT-T3):
    Each row's ``sequence``/``prev_hash``/``event_hash`` are computed BEFORE
    the row is inserted (using a locked read of the current chain tip), so
    writing an audit event is a single INSERT with no follow-up UPDATE. This
    matters because the DB-level append-only trigger (see
    app.core.audit_guard) blocks ALL updates to audit_log, including ones
    from this module itself — an earlier version of this code flushed the
    row first to get its autoincrement id, then mutated the hash fields
    in-place, which required a second UPDATE and would have been rejected
    by that trigger.
"""
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.audit_buffer import audit_db_circuit, buffer_event
from app.core.circuit_breaker import State
from app.core.config import settings
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


class AuditBufferFullError(Exception):
    """Raised by ingest_audit_event_durable when the DB write fails and the
    durable fallback buffer is also at capacity — the caller has nowhere
    left to safely hold the event."""

    def __init__(self, correlation_id: str):
        self.correlation_id = correlation_id
        super().__init__(f"Audit durability buffer is full (correlation_id={correlation_id})")


def _next_sequence_and_prev_hash(db: Session) -> Tuple[int, Optional[str]]:
    """Read the current chain tip under a row lock so concurrent writers
    serialize on sequence assignment instead of forking the hash chain.

    SQLite (used in tests) has no row-level locking — ``with_for_update()``
    compiles to a no-op there, but SQLite's single-writer lock at the
    connection level gives the same serialization guarantee.
    """
    prev = (
        db.query(AuditLog)
        .order_by(AuditLog.id.desc())
        .with_for_update()
        .first()
    )
    if prev is not None and prev.sequence:
        return prev.sequence + 1, prev.event_hash
    return 1, None


def _hash_event(entry: AuditLog, sequence: int, prev_hash: Optional[str]) -> str:
    """event_hash = SHA256(canonical_json_of_event + "|" + prev_hash).

    Uses only fields known before insert (sequence, not the DB-assigned id)
    so the hash can be computed and stored in the same INSERT as the row
    itself. Canonical JSON (sorted keys) keeps hashing deterministic.

    Deliberately excludes ``created_at``: SQLite (used in tests, and a
    supported dialect generally) doesn't preserve tzinfo through its
    DATETIME storage, so a timestamp hashed from the in-memory object at
    write time can come back different after a round-trip through the DB
    (session objects expire and re-fetch on commit) — that would make
    verify_hash_chain report tampering on rows nobody touched. sequence
    already gives total ordering, so a timestamp adds nothing to the
    tamper-evidence guarantee.
    """
    data = {
        "sequence": sequence,
        "event_type": entry.event_type,
        "actor": entry.actor,
        "module": entry.module,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "target_name": entry.target_name,
        "before_summary": entry.before_summary,
        "after_summary": entry.after_summary,
        "correlation_id": entry.correlation_id,
        "outcome": entry.outcome,
        "summary": entry.summary,
        "duration_ms": entry.duration_ms,
        "metadata": entry.event_metadata,
    }
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(f"{canonical}|{prev_hash or ''}".encode("utf-8")).hexdigest()


def _write_audit_row(
    db: Session,
    event_type: str,
    actor: str = "system",
    module: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[Any] = None,
    target_name: Optional[str] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
    outcome: str = "success",
    summary: Optional[str] = None,
    duration_ms: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    connection_id: Optional[int] = None,
    connection_name: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    status: str = "success",
) -> AuditLog:
    """Insert one audit row with its hash-chain link precomputed. Raises on failure.

    Defaults mirror emit_audit_event's — this is also called with whatever
    partial kwargs a buffered event dict happens to carry (AUDIT-T2's flush
    task), which may not set every field.
    """
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())
    target_id_str = str(target_id) if target_id is not None else None

    with db.begin_nested():
        sequence, prev_hash = _next_sequence_and_prev_hash(db)
        entry = AuditLog(
            event_type=event_type,
            actor=actor,
            module=module,
            target_type=target_type,
            target_id=target_id_str,
            target_name=target_name,
            before_summary=before,
            after_summary=after,
            correlation_id=correlation_id,
            outcome=outcome,
            summary=summary,
            duration_ms=duration_ms,
            event_metadata=metadata,
            sequence=sequence,
            prev_hash=prev_hash,
            created_at=datetime.now(timezone.utc),
            # Legacy fields
            connection_id=connection_id,
            connection_name=connection_name,
            payload=payload,
            status=status,
        )
        entry.event_hash = _hash_event(entry, sequence, prev_hash)
        db.add(entry)
        db.flush()

    return entry


def emit_audit_event(
    db: Session,
    event_type: str,
    actor: str = "system",
    module: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[Any] = None,
    target_name: Optional[str] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
    outcome: str = "success",
    summary: Optional[str] = None,
    duration_ms: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    # Legacy parameters (backward compat)
    connection_id: Optional[int] = None,
    connection_name: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    status: str = "success",
) -> str:
    """Emit a canonical audit event. Never raises (see module docstring).

    Returns the correlation_id (auto-generated if not provided).
    All modules should use this function instead of directly creating AuditLog rows.
    """
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())

    try:
        _write_audit_row(
            db, event_type=event_type, actor=actor, module=module,
            target_type=target_type, target_id=target_id, target_name=target_name,
            before=before, after=after, correlation_id=correlation_id,
            outcome=outcome, summary=summary, duration_ms=duration_ms, metadata=metadata,
            connection_id=connection_id, connection_name=connection_name,
            payload=payload, status=status,
        )
    except Exception as exc:
        logger.warning("Failed to write audit log (%s): %s", event_type, exc)

    return correlation_id


def record_audit(
    db: Session,
    event_type: str,
    actor: str = "admin",
    connection_id: Optional[int] = None,
    connection_name: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    status: str = "success",
    duration_ms: Optional[int] = None,
) -> None:
    """Legacy audit helper — delegates to emit_audit_event for backward compatibility."""
    emit_audit_event(
        db=db,
        event_type=event_type,
        actor=actor,
        module="legacy",
        connection_id=connection_id,
        connection_name=connection_name,
        payload=payload,
        outcome=status,
        status=status,
        duration_ms=duration_ms,
    )


def ingest_audit_event_durable(
    db: Session,
    event_type: str,
    actor: str = "system",
    module: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[Any] = None,
    target_name: Optional[str] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
    outcome: str = "success",
    summary: Optional[str] = None,
    duration_ms: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    connection_id: Optional[int] = None,
    connection_name: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    status: str = "success",
) -> Tuple[str, bool]:
    """Write one event for the AUDIT-T2 ingestion endpoint.

    Unlike emit_audit_event, this DOES surface failure: if the DB write
    fails (or the circuit is already open) it falls back to the bounded
    in-process buffer (app.core.audit_buffer) so app.tasks.audit_tasks
    .flush_audit_buffer_task can retry it once the DB recovers. Raises
    AuditBufferFullError only if that fallback buffer is also full — the
    router turns that into a 503 (backpressure).

    Returns (correlation_id, was_buffered).
    """
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())

    kwargs: Dict[str, Any] = dict(
        event_type=event_type, actor=actor, module=module, target_type=target_type,
        target_id=target_id, target_name=target_name, before=before, after=after,
        correlation_id=correlation_id, outcome=outcome, summary=summary,
        duration_ms=duration_ms, metadata=metadata, connection_id=connection_id,
        connection_name=connection_name, payload=payload, status=status,
    )

    if audit_db_circuit.state == State.OPEN:
        if not buffer_event(kwargs):
            raise AuditBufferFullError(correlation_id)
        return correlation_id, True

    last_exc: Optional[Exception] = None
    for attempt in range(settings.AUDIT_DB_WRITE_MAX_RETRIES + 1):
        try:
            audit_db_circuit.call(_write_audit_row, db, **kwargs)
            return correlation_id, False
        except Exception as exc:
            last_exc = exc
            if attempt < settings.AUDIT_DB_WRITE_MAX_RETRIES:
                time.sleep(0.05 * (2 ** attempt))

    logger.warning(
        "Audit ingestion DB write failed after %d retries (%s): %s — buffering",
        settings.AUDIT_DB_WRITE_MAX_RETRIES, event_type, last_exc,
    )
    if not buffer_event(kwargs):
        raise AuditBufferFullError(correlation_id) from last_exc
    return correlation_id, True


def verify_hash_chain(db: Session) -> Dict[str, Any]:
    """Verify the integrity of the entire audit hash chain (AUDIT-T3).

    Walks the chain from the first event forward, recomputing hashes
    and comparing with stored values.
    """
    entries = db.query(AuditLog).order_by(AuditLog.id.asc()).all()

    if not entries:
        return {"valid": True, "total_events": 0, "verified_events": 0}

    tampered: list[int] = []
    chain_broken_at: Optional[int] = None
    prev_hash: Optional[str] = None

    for entry in entries:
        # Skip entries without hash (pre-hash-chain legacy events, or events
        # written by modules that haven't adopted emit_audit_event yet).
        if not entry.event_hash:
            prev_hash = None
            continue

        expected_hash = _hash_event(entry, entry.sequence, prev_hash)

        if entry.event_hash != expected_hash:
            tampered.append(entry.id)
            if chain_broken_at is None:
                chain_broken_at = entry.id

        if entry.prev_hash != prev_hash:
            # Chain continuity broken
            if chain_broken_at is None:
                chain_broken_at = entry.id

        prev_hash = entry.event_hash

    return {
        "valid": len(tampered) == 0 and chain_broken_at is None,
        "total_events": len(entries),
        "verified_events": len(entries) - len(tampered),
        "chain_broken_at": chain_broken_at,
        "tampered_events": tampered,
    }
