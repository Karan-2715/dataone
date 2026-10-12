"""User <-> Role assignment + User.role cache sync (SEC-T1, SEC-T6)."""
import pytest
from fastapi import HTTPException

from app.services.rbac_service import RoleCRUD, UserRoleService


def test_backfill_gives_every_user_a_matching_role(admin, analyst, viewer, seeded):
    admin_roles = UserRoleService.get_user_roles(seeded, admin.id)["roles"]
    assert [r["name"] for r in admin_roles] == ["admin"]

    viewer_roles = UserRoleService.get_user_roles(seeded, viewer.id)["roles"]
    assert [r["name"] for r in viewer_roles] == ["viewer"]


def test_assign_additional_role_and_sync_cache_picks_highest_privilege(seeded, viewer):
    admin_role = next(r for r in RoleCRUD.list_roles(seeded) if r["name"] == "admin")
    UserRoleService.assign_role(seeded, viewer.id, admin_role["id"], actor="admin@test.local")

    seeded.refresh(viewer)
    assert viewer.role == "admin"  # highest-privilege canonical role wins the cache
    roles = {r["name"] for r in UserRoleService.get_user_roles(seeded, viewer.id)["roles"]}
    assert roles == {"viewer", "admin"}


def test_revoke_role_syncs_cache_back_down(seeded, viewer):
    admin_role = next(r for r in RoleCRUD.list_roles(seeded) if r["name"] == "admin")
    UserRoleService.assign_role(seeded, viewer.id, admin_role["id"], actor="admin@test.local")
    seeded.refresh(viewer)
    assert viewer.role == "admin"

    UserRoleService.revoke_role(seeded, viewer.id, admin_role["id"], confirm=True, actor="admin@test.local")
    seeded.refresh(viewer)
    assert viewer.role == "viewer"


def test_revoke_last_role_requires_confirm(seeded, viewer):
    viewer_role = next(r for r in RoleCRUD.list_roles(seeded) if r["name"] == "viewer")
    result = UserRoleService.revoke_role(seeded, viewer.id, viewer_role["id"], confirm=False, actor="admin@test.local")
    assert result["requires_confirm"] is True

    result2 = UserRoleService.revoke_role(seeded, viewer.id, viewer_role["id"], confirm=True, actor="admin@test.local")
    assert result2["roles"] == []


def test_revoke_nonexistent_assignment_404s(seeded, viewer):
    admin_role = next(r for r in RoleCRUD.list_roles(seeded) if r["name"] == "admin")
    with pytest.raises(HTTPException) as e:
        UserRoleService.revoke_role(seeded, viewer.id, admin_role["id"], confirm=True, actor="admin@test.local")
    assert e.value.status_code == 404


def test_effective_permissions_shape(seeded, admin):
    result = UserRoleService.effective_permissions(seeded, admin.id)
    assert result["roles"] == ["admin"]
    assert result["modules"]["security"]["admin"]["granted"] is True
    assert result["modules"]["security"]["admin"]["via_roles"] == ["admin"]


def test_effective_permissions_denies_by_default_for_viewer(seeded, viewer):
    result = UserRoleService.effective_permissions(seeded, viewer.id)
    assert result["modules"]["pipelines"]["delete"]["granted"] is False
    assert result["modules"]["pipelines"]["view"]["granted"] is True
