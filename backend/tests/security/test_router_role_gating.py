"""Router-level tests: privileged-change gating (SEC-T7, AC3), audit
emission (SEC-T8, AC4), and authZ-check contract (SEC-T2)."""
import pytest
from fastapi.testclient import TestClient

from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app
from app.models.audit import AuditLog
from app.services.rbac_service import RoleCRUD


def _client_for(db, user):
    def _override_user():
        return user

    def _override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[db_module.get_db] = _override_db
    return TestClient(app)


@pytest.fixture()
def client_viewer(seeded, viewer):
    c = _client_for(seeded, viewer)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_analyst(seeded, analyst):
    c = _client_for(seeded, analyst)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_admin(seeded, admin):
    c = _client_for(seeded, admin)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


# ── Privileged-change gating (AC3): non-elevated roles blocked ─────────────


def test_analyst_cannot_create_role(client_analyst):
    resp = client_analyst.post("/api/v1/roles/", json={"name": "custom", "description": None})
    assert resp.status_code == 403


def test_viewer_cannot_set_role_permissions(client_viewer, seeded):
    role = next(r for r in RoleCRUD.list_roles(seeded) if r["name"] == "viewer")
    resp = client_viewer.put(f"/api/v1/roles/{role['id']}/permissions", json={"permission_ids": []})
    assert resp.status_code == 403


def test_analyst_cannot_create_masking_policy(client_analyst, sales_connection):
    resp = client_analyst.post("/api/v1/policies/masking", json={
        "connection_id": sales_connection.id, "table_name": "sales",
        "column_name": "owner_email", "masking_type": "redact", "exempt_roles": [],
    })
    assert resp.status_code == 403


def test_analyst_cannot_assign_user_role(client_analyst, viewer, seeded):
    role = next(r for r in RoleCRUD.list_roles(seeded) if r["name"] == "admin")
    resp = client_analyst.post(f"/api/v1/users/{viewer.id}/roles", json={"role_id": role["id"]})
    assert resp.status_code == 403


def test_admin_can_create_role(client_admin):
    resp = client_admin.post("/api/v1/roles/", json={"name": "custom", "description": "test role"})
    assert resp.status_code == 201


# ── Read endpoints are open to any authenticated role ───────────────────────


def test_viewer_can_list_roles(client_viewer):
    resp = client_viewer.get("/api/v1/roles/")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_viewer_can_read_effective_permissions(client_viewer, viewer):
    resp = client_viewer.get(f"/api/v1/users/{viewer.id}/effective-permissions")
    assert resp.status_code == 200
    assert resp.json()["modules"]["pipelines"]["delete"]["granted"] is False


# ── AuthZ-check contract (SEC-T2/FR6) ───────────────────────────────────────


def test_authz_check_deny_by_default(client_viewer):
    resp = client_viewer.post("/api/v1/authz/check", json={"module": "pipelines", "action": "delete"})
    assert resp.status_code == 200
    assert resp.json()["allowed"] is False


def test_authz_check_admin_allowed(client_admin):
    resp = client_admin.post("/api/v1/authz/check", json={"module": "pipelines", "action": "delete"})
    assert resp.json()["allowed"] is True


def test_authz_check_rejects_unknown_module(client_viewer):
    resp = client_viewer.post("/api/v1/authz/check", json={"module": "bogus", "action": "view"})
    assert resp.status_code == 422


# ── Delete-with-dependents confirm gate (mirrors connectors' pattern) ──────


def test_delete_role_with_users_requires_confirm_via_router(client_admin, seeded, viewer):
    role = RoleCRUD.create_role(seeded, name="custom", description=None, actor="admin@test.local")
    from app.models.security import UserRole
    seeded.add(UserRole(user_id=viewer.id, role_id=role.id))
    seeded.commit()

    resp = client_admin.delete(f"/api/v1/roles/{role.id}")
    assert resp.status_code == 200
    assert resp.json()["requires_confirm"] is True

    resp2 = client_admin.delete(f"/api/v1/roles/{role.id}?confirm=true")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "deleted"


# ── Audit emission (SEC-T8, AC4) ────────────────────────────────────────────


def test_role_creation_emits_audit_event(client_admin, seeded):
    client_admin.post("/api/v1/roles/", json={"name": "custom", "description": "x"})
    event = seeded.query(AuditLog).filter(AuditLog.event_type == "role_created").first()
    assert event is not None
    assert event.module == "security"
    assert event.after_summary["name"] == "custom"


def test_masking_policy_creation_emits_audit_event(client_admin, sales_connection, seeded):
    client_admin.post("/api/v1/policies/masking", json={
        "connection_id": sales_connection.id, "table_name": "sales",
        "column_name": "owner_email", "masking_type": "redact", "exempt_roles": [],
    })
    event = seeded.query(AuditLog).filter(AuditLog.event_type == "masking_policy_created").first()
    assert event is not None
    assert event.target_name == "sales.owner_email"


def test_user_role_assignment_emits_audit_event(client_admin, viewer, seeded):
    role = next(r for r in RoleCRUD.list_roles(seeded) if r["name"] == "analyst")
    client_admin.post(f"/api/v1/users/{viewer.id}/roles", json={"role_id": role["id"]})
    event = seeded.query(AuditLog).filter(AuditLog.event_type == "user_role_assigned").first()
    assert event is not None
    assert event.after_summary["role"] == "analyst"
