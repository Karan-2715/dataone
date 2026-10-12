"""AuthzService policy engine — deny-by-default + cache invalidation (SEC-T2, AC1)."""
from app.services.rbac_service import AuthzService, RoleCRUD, PermissionCRUD


def test_admin_allowed_everywhere(seeded, admin):
    allowed, _ = AuthzService.check(seeded, admin.id, "pipelines", "delete")
    assert allowed is True


def test_viewer_denied_by_default_on_write_actions(seeded, viewer):
    allowed, reason = AuthzService.check(seeded, viewer.id, "pipelines", "delete")
    assert allowed is False
    assert "denied" in reason


def test_viewer_allowed_view(seeded, viewer):
    allowed, _ = AuthzService.check(seeded, viewer.id, "pipelines", "view")
    assert allowed is True


def test_analyst_denied_admin_module_admin_action(seeded, analyst):
    allowed, _ = AuthzService.check(seeded, analyst.id, "security", "admin")
    assert allowed is False


def test_unknown_module_denied(seeded, viewer):
    allowed, reason = AuthzService.check(seeded, viewer.id, "not_a_real_module", "view")
    assert allowed is False
    assert "unknown module" in reason


def test_unknown_action_denied(seeded, viewer):
    allowed, reason = AuthzService.check(seeded, viewer.id, "pipelines", "not_a_real_action")
    assert allowed is False
    assert "unknown action" in reason


def test_result_is_cached_until_version_bumps(seeded, viewer):
    allowed, _ = AuthzService.check(seeded, viewer.id, "pipelines", "delete")
    assert allowed is False
    assert viewer.id in AuthzService._cache

    # Grant delete to viewer's role directly, bypassing RoleCRUD (which would
    # bump the version itself) to prove the cache is what's stale here.
    viewer_role = next(r for r in RoleCRUD.list_roles(seeded) if r["name"] == "viewer")
    delete_perm = next(p for p in PermissionCRUD.list_permissions(seeded) if p.module == "pipelines" and p.action == "delete")
    from app.models.security import RolePermission
    seeded.add(RolePermission(role_id=viewer_role["id"], permission_id=delete_perm.id))
    seeded.commit()

    still_cached, _ = AuthzService.check(seeded, viewer.id, "pipelines", "delete")
    assert still_cached is False  # cache not yet invalidated

    AuthzService.bump_version()
    now_allowed, _ = AuthzService.check(seeded, viewer.id, "pipelines", "delete")
    assert now_allowed is True


def test_set_role_permissions_bumps_cache_version(seeded, viewer):
    allowed, _ = AuthzService.check(seeded, viewer.id, "pipelines", "delete")
    assert allowed is False

    viewer_role = next(r for r in RoleCRUD.list_roles(seeded) if r["name"] == "viewer")
    all_perm_ids = [p.id for p in PermissionCRUD.list_permissions(seeded)]
    RoleCRUD.set_role_permissions(seeded, viewer_role["id"], permission_ids=all_perm_ids, actor="admin@test.local")

    allowed_after, _ = AuthzService.check(seeded, viewer.id, "pipelines", "delete")
    assert allowed_after is True
