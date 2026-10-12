"""ACI.dev client service (aci_integration_tasks #2).

The single chokepoint for every ACI call in this codebase — mirroring how
frontend/src/lib/api.ts is the single chokepoint for HTTP on the frontend
side. Wraps ACI's Python SDK (`aci-sdk`: functions.search / functions.execute
/ linked_accounts.list) behind:

- the existing CircuitBreaker class (same pattern as ollama_circuit — an ACI
  outage degrades gracefully, never cascades into unrelated features), and
- retries with exponential backoff on transient failures (CLAUDE.md
  non-negotiable for external calls).

The SDK import is deferred so the module imports cleanly (and tests can
stub the client) even when the `aci-sdk` package isn't installed. An unset
ACI_API_KEY means "integration disabled": calls raise AciNotConfigured
immediately with a clear message rather than a mystery auth failure.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpen  # noqa: F401 (re-exported for callers)
from app.core.config import settings

logger = logging.getLogger(__name__)

aci_circuit = CircuitBreaker("aci", failure_threshold=5, reset_timeout=30.0)


class AciNotConfigured(Exception):
    """Raised when ACI_API_KEY is unset — the integration is disabled."""


class AciClientService:
    """Thin wrapper over the ACI SDK. All public methods are breaker-guarded
    and retried; they raise CircuitBreakerOpen fast when the circuit is open."""

    def __init__(self) -> None:
        self._client: Any = None

    # ── client construction (deferred SDK import) ─────────────────────────

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not settings.ACI_API_KEY:
            raise AciNotConfigured(
                "ACI integration is not configured (ACI_API_KEY is unset) — "
                "set ACI_API_KEY/ACI_BASE_URL to enable external tool calls"
            )
        from aci import ACI  # deferred: optional dependency

        try:
            self._client = ACI(api_key=settings.ACI_API_KEY,
                               base_url=settings.ACI_BASE_URL)
        except TypeError:
            # Older SDK versions take the base URL from an env var only.
            self._client = ACI(api_key=settings.ACI_API_KEY)
        return self._client

    # ── retry/breaker plumbing ────────────────────────────────────────────

    def _call(self, op_name: str, fn) -> Any:
        """Breaker-guarded call with exponential-backoff retries. A call
        attempted while the breaker is open fails fast (CircuitBreakerOpen)
        without consuming a retry."""
        last_exc: Optional[Exception] = None
        for attempt in range(settings.ACI_MAX_RETRIES + 1):
            try:
                result = aci_circuit.call(fn)
                logger.info("[aci] op=%s outcome=success attempt=%d", op_name, attempt + 1)
                return result
            except CircuitBreakerOpen:
                logger.warning("[aci] op=%s circuit open — failing fast", op_name)
                raise
            except AciNotConfigured:
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning("[aci] op=%s failed (attempt %d/%d): %s",
                               op_name, attempt + 1, settings.ACI_MAX_RETRIES + 1, exc)
                if attempt < settings.ACI_MAX_RETRIES:
                    time.sleep(2 ** attempt)
        raise last_exc  # type: ignore[misc]

    # ── public API ────────────────────────────────────────────────────────

    def search_tools(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """ACI meta-function tool discovery — dynamic, not a hand-maintained
        static list of 600+ schemas (FR2)."""
        logger.info("[aci] op=search_tools query=%r", query[:120])
        # Resolve the client OUTSIDE the breaker: an unset ACI_API_KEY is a
        # configuration state, not a service outage, so AciNotConfigured must
        # not count as a breaker failure (that would flip the clear
        # "not configured" error into a misleading "circuit open" after a few
        # calls, and pollute the breaker before ACI is ever configured).
        client = self._get_client()

        def _do():
            results = client.functions.search(intent=query, limit=limit)
            return [r if isinstance(r, dict) else getattr(r, "__dict__", {"name": str(r)})
                    for r in results]

        return self._call("search_tools", _do)

    def execute_tool(self, tool_name: str, params: Dict[str, Any],
                     linked_account_owner_id: Optional[str] = None) -> Dict[str, Any]:
        """Execute one external tool call through ACI."""
        owner = linked_account_owner_id or settings.ACI_LINKED_ACCOUNT_OWNER_ID
        logger.info("[aci] op=execute_tool tool=%s owner=%s", tool_name, owner)
        client = self._get_client()  # config check outside the breaker (see search_tools)

        def _do():
            result = client.functions.execute(
                function_name=tool_name,
                function_arguments=params,
                linked_account_owner_id=owner,
            )
            if isinstance(result, dict):
                return result
            return {"success": getattr(result, "success", True),
                    "data": getattr(result, "data", None),
                    "error": getattr(result, "error", None)}

        return self._call("execute_tool", _do)

    def list_linked_accounts(self) -> List[Dict[str, Any]]:
        """Linked external accounts — for the integrations frontend surface."""
        logger.info("[aci] op=list_linked_accounts")
        client = self._get_client()  # config check outside the breaker (see search_tools)

        def _do():
            accounts = client.linked_accounts.list()
            out: List[Dict[str, Any]] = []
            for a in accounts:
                if isinstance(a, dict):
                    out.append(a)
                else:
                    out.append({
                        "id": getattr(a, "id", None),
                        "app_name": getattr(a, "app_name", None),
                        "linked_account_owner_id": getattr(a, "linked_account_owner_id", None),
                        "enabled": getattr(a, "enabled", None),
                    })
            return out

        return self._call("list_linked_accounts", _do)


# Module-level singleton, same convention as ollama_circuit.
aci_client = AciClientService()
