"""Audit-event tests (aci_integration_tasks #9): every ACI-mediated action
is reconstructable from dataPlane's own Audit Trail with module=aci_integration."""
from __future__ import annotations

from app.models.audit import AuditLog
from app.services.autopilot_registry import check_action_allowed


def test_notify_out_task_audits_dispatch(db, fake_aci, monkeypatch):
    from app.core import database as db_module
    from app.tasks.aci_tasks import notify_out_task

    class _NoClose:
        def __init__(self, s):
            self._s = s

        def __getattr__(self, name):
            if name == "close":
                return lambda: None
            return getattr(self._s, name)

    monkeypatch.setattr(db_module, "SessionLocal", lambda: _NoClose(db))
    result = notify_out_task.run(event_key="pipeline:run_failure",
                                 title="run failed", body="", link="http://x")
    assert result["status"] == "sent"
    row = db.query(AuditLog).filter(AuditLog.event_type == "aci.notify_dispatched").one()
    assert row.module == "aci_integration"
    assert row.event_metadata["event_key"] == "pipeline:run_failure"
    assert row.event_metadata["destination"] == "#dataplane-internal"


def test_notify_out_task_audits_failure(db, fake_aci, monkeypatch):
    from app.core import database as db_module
    from app.tasks.aci_tasks import notify_out_task

    class _NoClose:
        def __init__(self, s):
            self._s = s

        def __getattr__(self, name):
            if name == "close":
                return lambda: None
            return getattr(self._s, name)

    monkeypatch.setattr(db_module, "SessionLocal", lambda: _NoClose(db))
    fake_aci.fail_with = ConnectionError("aci down")
    monkeypatch.setattr("app.services.aci_client_service.time.sleep", lambda s: None)

    result = notify_out_task.run(event_key="pipeline:run_failure",
                                 title="run failed")
    assert result["status"] == "failed"
    rows = db.query(AuditLog).filter(AuditLog.event_type == "aci.notify_failed").all()
    assert rows
    assert rows[0].module == "aci_integration"
    assert rows[0].outcome == "failure"
    assert rows[0].event_metadata["error"]


def test_executed_external_action_audited_with_destination(db, fake_aci):
    spec = check_action_allowed("external_message_send")
    spec.execute(db, {"destination": "#data-governance", "body": "hello"}, "admin@x")
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "aci.external_action_executed")
        .one()
    )
    assert row.module == "aci_integration"
    assert row.event_metadata["action_type"] == "external_message_send"
    assert row.event_metadata["destination"] == "#data-governance"
    assert row.outcome == "success"


def test_executed_email_audited(db, fake_aci):
    spec = check_action_allowed("external_email_send")
    spec.execute(db, {"to": "team@example.com", "subject": "s", "body": "b"}, "admin@x")
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "aci.external_action_executed")
        .one()
    )
    assert row.event_metadata["destination"] == "team@example.com"
