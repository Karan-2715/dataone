"""Client service tests (aci_integration_tasks #2): SDK boundary mocked,
circuit-breaker open/fail-fast, retry-with-backoff on transient failure."""
from __future__ import annotations

import pytest

from app.core.circuit_breaker import CircuitBreakerOpen
from app.services.aci_client_service import (
    AciClientService,
    AciNotConfigured,
    aci_client,
)


def test_search_tools_returns_matches(fake_aci):
    tools = aci_client.search_tools("post a message to slack")
    assert tools == [{"name": "SLACK__CHAT_POST_MESSAGE", "description": "Post a message"}]


def test_execute_tool_passes_owner_and_args(fake_aci):
    result = aci_client.execute_tool("SLACK__CHAT_POST_MESSAGE",
                                     {"channel": "#x", "text": "hi"})
    assert result["success"] is True
    call = fake_aci.executed[0]
    assert call["function_name"] == "SLACK__CHAT_POST_MESSAGE"
    assert call["linked_account_owner_id"] == "dataone"


def test_list_linked_accounts(fake_aci):
    accounts = aci_client.list_linked_accounts()
    assert accounts[0]["app_name"] == "SLACK"


def test_unconfigured_raises_clear_error(monkeypatch):
    # No fake client here: the REAL _get_client must refuse before any SDK
    # import when ACI_API_KEY is unset.
    from app.core.config import settings
    monkeypatch.setattr(settings, "ACI_API_KEY", None)
    svc = AciClientService()  # fresh instance, no cached client
    with pytest.raises(AciNotConfigured):
        svc.search_tools("anything")


def test_unconfigured_calls_do_not_pollute_breaker(monkeypatch):
    """Regression (v4 bugs2 #1): an unset ACI_API_KEY is a configuration
    state, not an outage. Repeated unconfigured calls must keep raising the
    clear AciNotConfigured error and leave the breaker CLOSED — never flip to
    a misleading CircuitBreakerOpen, and never pre-trip the breaker before ACI
    is configured."""
    from app.core.circuit_breaker import State
    from app.core.config import settings
    from app.services import aci_client_service

    monkeypatch.setattr(settings, "ACI_API_KEY", None)
    svc = AciClientService()  # fresh instance, no cached client
    for _ in range(6):  # well past the test breaker's threshold of 3
        with pytest.raises(AciNotConfigured):
            svc.search_tools("anything")
    assert aci_client_service.aci_circuit.state == State.CLOSED


def test_retry_then_succeed_on_transient_failure(fake_aci, no_sleep, monkeypatch):
    calls = {"n": 0}
    real_search = fake_aci.functions.search

    class FlakyFunctions:
        def search(self, intent=None, limit=None, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("transient")
            return real_search(intent=intent, limit=limit)

    monkeypatch.setattr(type(fake_aci), "functions",
                        property(lambda self: FlakyFunctions()))
    tools = aci_client.search_tools("post to slack")
    assert calls["n"] == 2  # failed once, retried, succeeded
    assert tools


def test_circuit_opens_after_threshold_and_fails_fast(fake_aci, no_sleep):
    fake_aci.fail_with = ConnectionError("aci down")
    # threshold=3 (test breaker), 2 attempts per call: call 1 records two
    # failures; call 2's first attempt trips the breaker, so its retry —
    # and every later call — fails fast with CircuitBreakerOpen.
    with pytest.raises(ConnectionError):
        aci_client.search_tools("x")
    with pytest.raises((ConnectionError, CircuitBreakerOpen)):
        aci_client.search_tools("x")
    with pytest.raises(CircuitBreakerOpen):
        aci_client.search_tools("x")


def test_open_circuit_does_not_consume_retries(fake_aci, no_sleep):
    fake_aci.fail_with = ConnectionError("aci down")
    with pytest.raises(ConnectionError):
        aci_client.execute_tool("T", {})
    with pytest.raises((ConnectionError, CircuitBreakerOpen)):
        aci_client.execute_tool("T", {})
    fake_aci.fail_with = None  # service recovered, but breaker still open
    with pytest.raises(CircuitBreakerOpen):
        aci_client.execute_tool("T", {})
    assert fake_aci.executed == []  # nothing reached the SDK while open
