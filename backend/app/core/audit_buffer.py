"""Bounded in-process buffer + circuit breaker for durable audit ingestion.

Used by ``POST /audit/events`` (AUDIT-T2). When the DB write path for audit
events is failing (circuit OPEN, or a write raises after retries), the
ingestion endpoint holds the event here instead of dropping it, and
``app.tasks.audit_tasks.flush_audit_buffer_task`` (Celery beat) periodically
retries writing buffered events once the DB is healthy again.

This is an in-process buffer, not a broker-backed queue: it survives
transient DB outages while the app process stays up, but is lost on process
restart. A Redis/Celery-broker-backed queue would additionally survive
process restarts — that's a reasonable follow-up but adds real operational
complexity (see requirements-specs/audit_trail_tasks/02_ingestion_api_durable_buffering.md),
so it's out of scope here.

Only the dedicated ingestion endpoint uses this. The internal
``emit_audit_event``/``record_audit`` helper used by other services keeps its
existing swallow-and-log contract (see audit_helper.py) — that contract is
load-bearing for business-transaction atomicity and is intentionally left
unchanged.
"""
import logging
import threading
from collections import deque
from typing import Any, Dict, List

from app.core.circuit_breaker import CircuitBreaker
from app.core.config import settings

logger = logging.getLogger(__name__)

audit_db_circuit = CircuitBreaker(
    name="audit_db_write",
    failure_threshold=settings.AUDIT_DB_CIRCUIT_FAILURE_THRESHOLD,
    reset_timeout=settings.AUDIT_DB_CIRCUIT_RESET_TIMEOUT_SECONDS,
)

_lock = threading.Lock()
_buffer: deque = deque()


def buffer_event(event: Dict[str, Any]) -> bool:
    """Durably hold *event* pending a flush. Returns False if the buffer is full."""
    with _lock:
        if len(_buffer) >= settings.AUDIT_BUFFER_MAX_SIZE:
            return False
        _buffer.append(event)
        return True


def buffer_depth() -> int:
    with _lock:
        return len(_buffer)


def drain_buffer() -> List[Dict[str, Any]]:
    """Atomically remove and return every currently buffered event."""
    with _lock:
        items = list(_buffer)
        _buffer.clear()
        return items


def requeue(events: List[Dict[str, Any]]) -> int:
    """Push events back onto the buffer (e.g. a flush attempt failed again).

    Returns the number of events dropped because the buffer was full.
    """
    dropped = 0
    with _lock:
        for event in events:
            if len(_buffer) >= settings.AUDIT_BUFFER_MAX_SIZE:
                dropped += 1
                continue
            _buffer.append(event)
    if dropped:
        logger.error("Audit buffer full — dropped %d event(s) on requeue", dropped)
    return dropped
