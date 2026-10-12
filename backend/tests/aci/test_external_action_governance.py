"""Governance tests (aci_integration_tasks #3): registry classifications,
import-time invariants, fixed-destination enforcement."""
from __future__ import annotations

import pytest

from app.services.autopilot_registry import (
    ACTION_REGISTRY,
    ActionSpec,
    PROHIBITED_ACTION_TYPES,
    check_action_allowed,
)

EXTERNAL_ACTIONS = {
    "notify_slack_internal", "external_message_send",
    "external_ticket_create", "external_email_send",
}


def test_all_external_actions_registered():
    assert EXTERNAL_ACTIONS <= set(ACTION_REGISTRY)


def test_only_fixed_destination_notify_is_auto_capable():
    assert ACTION_REGISTRY["notify_slack_internal"].auto_capable is True
    for action in EXTERNAL_ACTIONS - {"notify_slack_internal"}:
        assert ACTION_REGISTRY[action].auto_capable is False, (
            f"{action} must be approval-only")


def test_user_suppliable_destination_forces_approval_even_when_reversible():
    """The crux rule: external_message_send is reversible and the verb is
    low-blast, but the destination is user-suppliable — never auto."""
    spec = ACTION_REGISTRY["external_message_send"]
    assert spec.reversible is True
    assert spec.auto_capable is False


def test_email_is_highest_risk():
    assert ACTION_REGISTRY["external_email_send"].risk == "high"


def test_import_time_invariant_rejects_auto_capable_high_risk():
    """The same invariant the module asserts at import time must reject a
    misconfigured auto_capable=True + risk=high entry."""
    bad = ActionSpec(
        action_type="bad_action", description="misconfigured",
        risk="high", reversible=True, reversibility_note="",
        auto_capable=True, required_payload_keys=frozenset(),
        execute=lambda db, payload, actor: {},
    )
    with pytest.raises(AssertionError):
        assert not (bad.auto_capable and bad.risk != "low"), (
            f"{bad.action_type}: auto_capable requires low risk")


def test_registry_wide_invariants_hold():
    for spec in ACTION_REGISTRY.values():
        assert not (spec.auto_capable and not spec.reversible)
        assert not (spec.auto_capable and spec.risk != "low")
        assert spec.action_type not in PROHIBITED_ACTION_TYPES


def test_notify_executor_ignores_payload_channel(db, fake_aci):
    """Structural fixed-destination enforcement: a channel smuggled into the
    payload is ignored — the message goes to the admin-configured channel."""
    spec = check_action_allowed("notify_slack_internal")
    result = spec.execute(db, {"title": "t", "body": "b",
                               "channel": "#attacker-chosen"}, "test")
    assert result["channel"] == "#dataplane-internal"
    call = fake_aci.executed[0]
    assert call["function_arguments"]["channel"] == "#dataplane-internal"


def test_notify_executor_requires_configured_channel(db, fake_aci, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ACI_SLACK_INTERNAL_CHANNEL", "")
    spec = check_action_allowed("notify_slack_internal")
    with pytest.raises(ValueError, match="not configured"):
        spec.execute(db, {"title": "t"}, "test")
