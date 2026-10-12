"""ai_autopilot_tasks #2: autonomy policy — fail-safe defaults, validation,
role gating, audit."""
from app.models.audit import AuditLog
from app.services.autopilot_registry import ACTION_REGISTRY
from app.services.autopilot_service import AutopilotService


def test_defaults_are_suggest_for_all_types(client_viewer):
    r = client_viewer.get("/api/v1/autopilot/policy")
    assert r.status_code == 200
    policies = r.json()["policies"]
    assert {p["action_type"] for p in policies} == set(ACTION_REGISTRY)
    assert all(p["autonomy"] == "suggest" for p in policies)
    # Registry metadata is merged in for the UI.
    assert all("auto_capable" in p and "reversibility_note" in p for p in policies)


def test_put_auto_rejected_for_non_auto_capable(client_admin):
    r = client_admin.put(
        "/api/v1/autopilot/policy/migration_execute",
        json={"autonomy": "auto"},
    )
    assert r.status_code == 422
    assert "not auto-capable" in r.json()["detail"]


def test_put_auto_allowed_for_auto_capable(client_admin, db):
    r = client_admin.put(
        "/api/v1/autopilot/policy/connector_health_check",
        json={"autonomy": "auto", "max_auto_per_hour": 5},
    )
    assert r.status_code == 200
    assert r.json()["autonomy"] == "auto"
    assert r.json()["max_auto_per_hour"] == 5
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "autopilot_policy_changed")
        .first()
    )
    assert audit is not None
    assert audit.payload["after"]["autonomy"] == "auto"


def test_put_policy_admin_only(client_analyst):
    r = client_analyst.put(
        "/api/v1/autopilot/policy/connector_health_check",
        json={"autonomy": "approve"},
    )
    assert r.status_code == 403


def test_put_unknown_action_404(client_admin):
    r = client_admin.put(
        "/api/v1/autopilot/policy/definitely_not_real",
        json={"autonomy": "suggest"},
    )
    assert r.status_code == 404


def test_put_prohibited_action_403(client_admin):
    r = client_admin.put(
        "/api/v1/autopilot/policy/credential_change",
        json={"autonomy": "suggest"},
    )
    assert r.status_code == 403


def test_put_invalid_level_422(client_admin):
    r = client_admin.put(
        "/api/v1/autopilot/policy/drift_rescan",
        json={"autonomy": "yolo"},
    )
    assert r.status_code == 422


def test_effective_policy_defaults(db):
    p = AutopilotService.get_effective_policy(db, "drift_rescan")
    assert p["autonomy"] == "suggest"
    assert p["max_auto_per_hour"] > 0
