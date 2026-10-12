"""Role + permission catalog CRUD (SEC-T1)."""
import pytest
from fastapi import HTTPException

from app.services.rbac_service import (
    ACTIONS, CANONICAL_ROLES, MODULES, PermissionCRUD, RoleCRUD,
)


def test_seeding_creates_full_permission_catalog(seeded):
    perms = PermissionCRUD.list_permissions(seeded)
    assert len(perms) == len(MODULES) * len(ACTIONS)


def test_seeding_creates_three_canonical_roles(seeded):
    roles = {r["name"] for r in RoleCRUD.list_roles(seeded)}
    assert roles == set(CANONICAL_ROLES)


def test_admin_role_has_every_permission(seeded):
    roles = {r["name"]: r for r in RoleCRUD.list_roles(seeded)}
    assert roles["admin"]["permission_count"] == len(MODULES) * len(ACTIONS)


def test_viewer_role_has_view_only(seeded):
    roles = {r["name"]: r for r in RoleCRUD.list_roles(seeded)}
    assert roles["viewer"]["permission_count"] == len(MODULES)


def test_create_role(seeded):
    role = RoleCRUD.create_role(seeded, name="auditor", description="Read-only compliance role", actor="admin@test.local")
    assert role.id is not None
    assert role.name == "auditor"


def test_create_role_rejects_duplicate_name(seeded):
    RoleCRUD.create_role(seeded, name="auditor", description=None, actor="admin@test.local")
    with pytest.raises(HTTPException) as e:
        RoleCRUD.create_role(seeded, name="auditor", description=None, actor="admin@test.local")
    assert e.value.status_code == 409


def test_cannot_rename_built_in_role(seeded):
    from app.models.security import Role
    admin_role = seeded.query(Role).filter(Role.name == "admin").first()
    with pytest.raises(HTTPException) as e:
        RoleCRUD.update_role(seeded, admin_role.id, name="superadmin", description=None, is_active=None, actor="x")
    assert e.value.status_code == 400


def test_cannot_deactivate_built_in_role(seeded):
    from app.models.security import Role
    admin_role = seeded.query(Role).filter(Role.name == "admin").first()
    with pytest.raises(HTTPException) as e:
        RoleCRUD.update_role(seeded, admin_role.id, name=None, description=None, is_active=False, actor="x")
    assert e.value.status_code == 400


def test_cannot_delete_built_in_role(seeded):
    from app.models.security import Role
    viewer_role = seeded.query(Role).filter(Role.name == "viewer").first()
    with pytest.raises(HTTPException) as e:
        RoleCRUD.delete_role(seeded, viewer_role.id, confirm=True, actor="x")
    assert e.value.status_code == 400


def test_delete_role_with_dependents_requires_confirm(seeded, viewer):
    from app.models.security import Role, UserRole
    role = RoleCRUD.create_role(seeded, name="custom", description=None, actor="admin@test.local")
    seeded.add(UserRole(user_id=viewer.id, role_id=role.id))
    seeded.commit()

    result = RoleCRUD.delete_role(seeded, role.id, confirm=False, actor="admin@test.local")
    assert result["requires_confirm"] is True
    assert seeded.query(Role).filter(Role.id == role.id).first() is not None

    result2 = RoleCRUD.delete_role(seeded, role.id, confirm=True, actor="admin@test.local")
    assert result2["status"] == "deleted"
    assert seeded.query(Role).filter(Role.id == role.id).first() is None


def test_set_role_permissions_replaces_full_set(seeded):
    role = RoleCRUD.create_role(seeded, name="custom", description=None, actor="admin@test.local")
    perms = PermissionCRUD.list_permissions(seeded)
    view_perms = [p.id for p in perms if p.action == "view"][:3]

    RoleCRUD.set_role_permissions(seeded, role.id, permission_ids=view_perms, actor="admin@test.local")
    assert set(PermissionCRUD.list_role_permission_ids(seeded, role.id)) == set(view_perms)

    RoleCRUD.set_role_permissions(seeded, role.id, permission_ids=[], actor="admin@test.local")
    assert PermissionCRUD.list_role_permission_ids(seeded, role.id) == []


def test_set_role_permissions_rejects_unknown_ids(seeded):
    role = RoleCRUD.create_role(seeded, name="custom", description=None, actor="admin@test.local")
    with pytest.raises(HTTPException) as e:
        RoleCRUD.set_role_permissions(seeded, role.id, permission_ids=[999999], actor="admin@test.local")
    assert e.value.status_code == 400
