from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class TestConnectionResult:
    """Structured outcome of a connectivity test (connector_tasks #4, FR4).

    On success the boolean check fields are all True. On failure, the
    fields describe how far the test got: reachable=False means the host
    couldn't be contacted at all; reachable=True + authenticated=False
    means the host answered but rejected the credentials; etc.
    """
    __test__ = False  # not a pytest class, despite the Test* name

    success: bool
    reachable: bool = True
    authenticated: bool = True
    database_accessible: bool = True
    version: Optional[str] = None
    latency_ms: Optional[int] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None


def classify_connection_error(error_msg: str) -> TestConnectionResult:
    """Map a driver error message onto a failed TestConnectionResult.

    Shared by every connector so the heuristic keyword chain lives in one
    place. Message text varies by driver version/locale — the error_code
    is the structured signal; the raw message rides along as detail.
    """
    msg = (error_msg or "").lower()
    if "timed out" in msg or "timeout" in msg:
        return TestConnectionResult(
            success=False, reachable=False, authenticated=False,
            database_accessible=False,
            error_message=error_msg, error_code="CONNECTION_TIMEOUT",
        )
    if ("could not connect" in msg or "connection refused" in msg
            or "could not translate host" in msg or "name or service not known"
            in msg or "nodename nor servname" in msg or "unreachable" in msg
            or "getaddrinfo" in msg or "unable to open database" in msg
            or "no such file" in msg or "can't connect" in msg):
        return TestConnectionResult(
            success=False, reachable=False, authenticated=False,
            database_accessible=False,
            error_message=error_msg, error_code="CONNECTION_REFUSED",
        )
    if ("authentication failed" in msg or "password" in msg
            or "access denied" in msg or "login" in msg
            or "invalid username" in msg or "ora-01017" in msg):
        return TestConnectionResult(
            success=False, reachable=True, authenticated=False,
            database_accessible=False,
            error_message=error_msg, error_code="AUTH_FAILED",
        )
    if ("database" in msg and ("does not exist" in msg or "not found" in msg
                               or "unknown" in msg)) or "read-only" in msg \
            or "in recovery" in msg or "readonly" in msg:
        return TestConnectionResult(
            success=False, reachable=True, authenticated=True,
            database_accessible=False,
            error_message=error_msg, error_code="DATABASE_UNAVAILABLE",
        )
    return TestConnectionResult(
        success=False, reachable=True, authenticated=True,
        database_accessible=False,
        error_message=error_msg, error_code="UNKNOWN_ERROR",
    )


class BaseConnector(ABC):
    """
    Abstract Base Class for all database connectors.
    Provides common interface for connection management and schema extraction.
    """

    @abstractmethod
    def connect(self) -> Any:
        """Establish connection to the target system and return handle/session."""
        pass

    @abstractmethod
    def test_connection(self) -> TestConnectionResult:
        """Test connectivity and return structured diagnostics.

        Implementations must never raise — every failure is caught and
        classified via classify_connection_error()."""
        pass

    @abstractmethod
    def get_tables(self) -> List[str]:
        """Fetch list of all table names inside the database/schema."""
        pass

    @abstractmethod
    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        """
        Fetch columns of a table returning list of dicts:
        [{'name': 'id', 'type': 'INT', 'nullable': False, 'primary_key': True,
          'foreign_keys': [{'references_table': 'orders', 'references_column': 'id'}]}]

        'foreign_keys' is optional (omit or return [] if the connector can't
        determine it) -- callers must not assume every implementation
        populates it.
        """
        pass

    @abstractmethod
    def close(self):
        """Close connection handles safely."""
        pass

    def profile_column(self, table: str, column: str,
                        sample_limit: int = 1000,
                        distinct_scan_limit: int = 100000) -> "ColumnProfileResult":
        """Profile a single column: null rate, distinct count, min/max, sample values.

        Not abstract — a column-metadata-only connector (if one is ever
        added) can skip profiling entirely by not overriding this, and
        callers see a clear "not implemented" error rather than a crash.
        Every connector shipped today overrides this (schema_intel_tasks #2).

        :param sample_limit: Max rows to sample for sample_values (Schema Intel
            Task #8 Decision 2 default: 1000). Sample values are used only for
            in-memory classification (Task #3) and must NEVER be persisted.
        :param distinct_scan_limit: Max rows scanned for COUNT(DISTINCT ...)
            (Task #8 Decision 2 default: 100000) — bounds an expensive query
            on large unindexed columns.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement profile_column")


@dataclass
class ColumnProfileResult:
    """Result of BaseConnector.profile_column(). ``sample_values`` is
    in-memory only (Schema Intel Task #8 Decision 1) — callers must persist
    only the aggregate fields, never this list."""
    null_count: int = 0
    null_rate: float = 0.0
    distinct_count: Optional[int] = None
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    sample_values: List[Any] = field(default_factory=list)
    sample_size_used: int = 0
    # Total table row count (agentic_dba_tasks #2) — every connector already
    # computes this for null_rate; exposing it enables uniqueness_ratio.
    row_count: Optional[int] = None
    error: Optional[str] = None
