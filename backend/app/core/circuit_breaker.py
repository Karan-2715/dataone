"""
Simple circuit breaker for external service calls.

States:
  CLOSED   — normal operation; failures are counted
  OPEN     — failing fast; no calls pass through until the reset timeout expires
  HALF_OPEN — one probe call allowed; success closes the circuit, failure re-opens it
"""
import logging
import threading
import time
from enum import Enum

logger = logging.getLogger(__name__)


class State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    """Raised when a call is attempted while the circuit is open."""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: float = 30.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._state = State.CLOSED
        self._failures = 0
        self._opened_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> State:
        with self._lock:
            if self._state == State.OPEN:
                if time.monotonic() - self._opened_at >= self.reset_timeout:
                    self._state = State.HALF_OPEN
                    logger.info("Circuit '%s' entering HALF_OPEN state", self.name)
            return self._state

    def record_success(self) -> None:
        with self._lock:
            if self._state in (State.HALF_OPEN, State.OPEN):
                logger.info("Circuit '%s' closed after successful probe", self.name)
            self._state = State.CLOSED
            self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == State.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = State.OPEN
                self._opened_at = time.monotonic()
                logger.warning(
                    "Circuit '%s' OPENED after %d failure(s)", self.name, self._failures
                )

    def call(self, func, *args, **kwargs):
        """Execute *func* under circuit-breaker protection."""
        current = self.state
        if current == State.OPEN:
            raise CircuitBreakerOpen(
                f"Circuit '{self.name}' is OPEN — failing fast"
            )
        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise


# Module-level singleton for Ollama
ollama_circuit = CircuitBreaker(
    name="ollama",
    failure_threshold=5,
    reset_timeout=30.0,
)
