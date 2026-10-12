"""RBAC + policy engine service (DP-SEC-001, SEC-T1/T2/T3/T4).

Contains: the static permission catalog + default-role seeding, Role/
UserRole/MaskingPolicy/RowAccessPolicy CRUD, and the ``AuthzService``
policy engine backing the ``POST /authz/check`` contract other modules
call. See app/models/security.py for the data model rationale.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.security import (
    MASKING_TYPES,
    MaskingPolicy,
    Permission,
    Role,
    RolePermission,
    RowAccessPolicy,
    UserRole,
)
from app.models.user import User
from app.services.audit_helper import emit_audit_event

logger = logging.getLogger(__name__)

MODULES = (
    "connectors", "pipelines", "mapper", "schema_intel", "query_studio",
    "askdata", "autopilot", "audit", "security", "viz",
)
ACTIONS = ("view", "create", "edit", "delete", "run", "publish", "admin")

CANONICAL_ROLES = ("admin", "analyst", "viewer")
# Highest-privilege first — used to pick the require_role()-compatible
# cache value when a user holds multiple roles.
_CANONICAL_PRECEDENCE = {"admin": 3, "analyst": 2, "viewer": 1}

_FILTER_OPERATORS = ("=", "!=", ">", "<", ">=", "<=", "in", "not in")

# Default grants for the 3 seeded roles. "admin" gets every (module, action)
# pair. "analyst" gets everything except delete/admin, and only view on the
# audit/security modules (those are administrative surfaces). "viewer" gets
# view-only everywhere.
_ANALYST_ACTIONS = {"view", "create", "edit", "run", "publish"}
_ANALYST_RESTRICTED_MODULES = {"audit", "security"}


def _default_role_permissions(role_name: str) -> Set[Tuple[str, str]]:
    if role_name == "admin":
        return {(m, a) for m in MODULES for a in ACTIONS}
    if role_name == "analyst":
        grants = set()
        for m in MODULES:
            actions = {"view"} if m in _ANALYST_RESTRICTED_MODULES else _ANALYST_ACTIONS
            grants |= {(m, a) for a in actions}
        return grants
    if role_name == "viewer":
        return {(m, "view") for m in MODULES}
    return set()


# ── Seeding (called once from app.main's lifespan) ─────────────────────────


def seed_permission_catalog(db: Session) -> None:
    existing = {(p.module, p.action) for p in db.query(Permission).all()}
    for module in MODULES:
        for action in ACTIONS:
            if (module, action) not in existing:
                db.add(Permission(module=module, action=action))
    db.commit()


def seed_default_roles(db: Session) -> None:
    perm_by_pair = {(p.module, p.action): p for p in db.query(Permission).all()}
    for role_name in CANONICAL_ROLES:
        role = db.query(Role).filter(Role.name == role_name).first()
        if role is None:
            role = Role(name=role_name, description=f"Built-in {role_name} role")
            db.add(role)
            db.flush()
        granted = {
            rp.permission_id
            for rp in db.query(RolePermission).filter(RolePermission.role_id == role.id)
        }
        for pair in _default_role_permissions(role_name):
            perm = perm_by_pair.get(pair)
            if perm is not None and perm.id not in granted:
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.commit()


def backfill_user_roles(db: Session) -> None:
    """Give every existing User a UserRole matching their current
    ``User.role`` string, if they don't already have any roles assigned."""
    role_by_name = {r.name: r for r in db.query(Role).filter(Role.name.in_(CANONICAL_ROLES)).all()}
    for user in db.query(User).all():
        has_role = db.query(UserRole).filter(UserRole.user_id == user.id).first()
        if has_role:
            continue
        role = role_by_name.get(user.role) or role_by_name.get("viewer")
        if role is not None:
            db.add(UserRole(user_id=user.id, role_id=role.id))
    db.commit()


def sync_user_role_cache(db: Session, user_id: int) -> None:
    """Recompute User.role from the user's assigned active roles, picking
    the highest-privilege canonical name. Custom (non-canonical) roles
    still enforce correctly through AuthzService.check, but don't change
    this legacy cache column — documented scope limit, see INDEX.md."""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return
    role_names = {
        ur.role.name
        for ur in db.query(UserRole).filter(UserRole.user_id == user_id).all()
        if ur.role is not None and ur.role.is_active
    }
    canonical_held = [r for r in role_names if r in _CANONICAL_PRECEDENCE]
    if canonical_held:
        user.role = max(canonical_held, key=lambda r: _CANONICAL_PRECEDENCE[r])
        db.commit()


# ── Role CRUD (SEC-T1, SEC-T5) ──────────────────────────────────────────────


class RoleCRUD:
    @staticmethod
    def list_roles(db: Session) -> List[Dict[str, Any]]:
        roles = db.query(Role).order_by(Role.id.asc()).all()
        return [RoleCRUD.to_dict(db, r) for r in roles]

    @staticmethod
    def to_dict(db: Session, role: Role) -> Dict[str, Any]:
        permission_count = (
            db.query(RolePermission).filter(RolePermission.role_id == role.id).count()
        )
        user_count = db.query(UserRole).filter(UserRole.role_id == role.id).count()
        return {
            "id": role.id,
            "name": role.name,
            "description": role.description,
            "is_active": role.is_active,
            "permission_count": permission_count,
            "user_count": user_count,
            "created_at": role.created_at,
            "updated_at": role.updated_at,
        }

    @staticmethod
    def get_role(db: Session, role_id: int) -> Role:
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None:
            raise HTTPException(status_code=404, detail=f"Role {role_id} not found")
        return role

    @staticmethod
    def create_role(db: Session, name: str, description: Optional[str], actor: str) -> Role:
        if db.query(Role).filter(Role.name == name).first():
            raise HTTPException(status_code=409, detail=f"Role '{name}' already exists")
        role = Role(name=name, description=description)
        db.add(role)
        db.flush()
        emit_audit_event(
            db, "role_created", actor=actor, module="security",
            target_type="role", target_id=role.id, target_name=role.name,
            after={"name": name, "description": description},
        )
        db.commit()
        AuthzService.bump_version()
        return role

    @staticmethod
    def update_role(
        db: Session, role_id: int, name: Optional[str], description: Optional[str],
        is_active: Optional[bool], actor: str,
    ) -> Role:
        role = RoleCRUD.get_role(db, role_id)
        before = {"name": role.name, "description": role.description, "is_active": role.is_active}
        if name is not None:
            if role.name in CANONICAL_ROLES and name != role.name:
                raise HTTPException(status_code=400, detail=f"Cannot rename built-in role '{role.name}'")
            role.name = name
        if description is not None:
            role.description = description
        if is_active is not None:
            if role.name in CANONICAL_ROLES and not is_active:
                raise HTTPException(status_code=400, detail=f"Cannot deactivate built-in role '{role.name}'")
            role.is_active = is_active
        db.flush()
        after = {"name": role.name, "description": role.description, "is_active": role.is_active}
        emit_audit_event(
            db, "role_updated", actor=actor, module="security",
            target_type="role", target_id=role.id, target_name=role.name,
            before=before, after=after,
        )
        db.commit()
        AuthzService.bump_version()
        return role

    @staticmethod
    def delete_role(db: Session, role_id: int, confirm: bool, actor: str) -> Dict[str, Any]:
        role = RoleCRUD.get_role(db, role_id)
        if role.name in CANONICAL_ROLES:
            raise HTTPException(status_code=400, detail=f"Cannot delete built-in role '{role.name}'")
        assigned_users = db.query(UserRole).filter(UserRole.role_id == role_id).all()
        if assigned_users and not confirm:
            return {
                "warning": (
                    f"This role is assigned to {len(assigned_users)} user(s). "
                    "Deleting it will revoke their access from this role. "
                    "Repeat the request with ?confirm=true to proceed."
                ),
                "assigned_user_ids": [ur.user_id for ur in assigned_users],
                "requires_confirm": True,
            }
        affected_user_ids = [ur.user_id for ur in assigned_users]
        db.delete(role)
        emit_audit_event(
            db, "role_deleted", actor=actor, module="security",
            target_type="role", target_id=role_id, target_name=role.name,
            before={"name": role.name}, metadata={"affected_user_ids": affected_user_ids},
        )
        db.commit()
        for uid in affected_user_ids:
            sync_user_role_cache(db, uid)
        AuthzService.bump_version()
        return {"status": "deleted", "id": role_id, "affected_users": len(affected_user_ids)}

    @staticmethod
    def set_role_permissions(db: Session, role_id: int, permission_ids: List[int], actor: str) -> Role:
        role = RoleCRUD.get_role(db, role_id)
        valid_ids = {p.id for p in db.query(Permission).filter(Permission.id.in_(permission_ids)).all()}
        unknown = set(permission_ids) - valid_ids
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown permission id(s): {sorted(unknown)}")

        existing = db.query(RolePermission).filter(RolePermission.role_id == role_id).all()
        before_ids = sorted(rp.permission_id for rp in existing)
        for rp in existing:
            db.delete(rp)
        db.flush()
        for pid in sorted(set(permission_ids)):
            db.add(RolePermission(role_id=role_id, permission_id=pid))
        db.flush()

        perm_by_id = {p.id: p for p in db.query(Permission).all()}
        before_labels = sorted(f"{perm_by_id[i].module}:{perm_by_id[i].action}" for i in before_ids if i in perm_by_id)
        after_labels = sorted(f"{perm_by_id[i].module}:{perm_by_id[i].action}" for i in permission_ids if i in perm_by_id)
        emit_audit_event(
            db, "role_permissions_updated", actor=actor, module="security",
            target_type="role", target_id=role.id, target_name=role.name,
            before={"permissions": before_labels}, after={"permissions": after_labels},
        )
        db.commit()
        AuthzService.bump_version()
        return role


class PermissionCRUD:
    @staticmethod
    def list_permissions(db: Session) -> List[Permission]:
        return db.query(Permission).order_by(Permission.module.asc(), Permission.action.asc()).all()

    @staticmethod
    def list_role_permission_ids(db: Session, role_id: int) -> List[int]:
        return [
            rp.permission_id
            for rp in db.query(RolePermission).filter(RolePermission.role_id == role_id).all()
        ]


# ── User <-> Role assignment (SEC-T1, SEC-T5) ───────────────────────────────


class UserRoleService:
    @staticmethod
    def list_users(db: Session) -> List[Dict[str, Any]]:
        users = db.query(User).order_by(User.id.asc()).all()
        out = []
        for u in users:
            roles = [ur.role.name for ur in db.query(UserRole).filter(UserRole.user_id == u.id).all() if ur.role]
            out.append({
                "id": u.id, "email": u.email, "cached_role": u.role,
                "is_active": u.is_active, "roles": roles,
            })
        return out

    @staticmethod
    def get_user(db: Session, user_id: int) -> User:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")
        return user

    @staticmethod
    def assign_role(db: Session, user_id: int, role_id: int, actor: str) -> Dict[str, Any]:
        user = UserRoleService.get_user(db, user_id)
        role = RoleCRUD.get_role(db, role_id)
        existing = db.query(UserRole).filter(
            UserRole.user_id == user_id, UserRole.role_id == role_id,
        ).first()
        if existing is None:
            db.add(UserRole(user_id=user_id, role_id=role_id))
            emit_audit_event(
                db, "user_role_assigned", actor=actor, module="security",
                target_type="user", target_id=user_id, target_name=user.email,
                after={"role": role.name},
            )
            db.commit()
            sync_user_role_cache(db, user_id)
            AuthzService.bump_version()
        return UserRoleService.get_user_roles(db, user_id)

    @staticmethod
    def revoke_role(db: Session, user_id: int, role_id: int, confirm: bool, actor: str) -> Dict[str, Any]:
        user = UserRoleService.get_user(db, user_id)
        role = RoleCRUD.get_role(db, role_id)
        link = db.query(UserRole).filter(
            UserRole.user_id == user_id, UserRole.role_id == role_id,
        ).first()
        if link is None:
            raise HTTPException(status_code=404, detail="User does not have this role")

        remaining = db.query(UserRole).filter(
            UserRole.user_id == user_id, UserRole.role_id != role_id,
        ).count()
        if remaining == 0 and not confirm:
            return {
                "warning": (
                    f"This is the only role assigned to {user.email}. Revoking it leaves "
                    "them with zero permissions (deny-by-default). Repeat the request "
                    "with ?confirm=true to proceed."
                ),
                "requires_confirm": True,
            }

        db.delete(link)
        emit_audit_event(
            db, "user_role_revoked", actor=actor, module="security",
            target_type="user", target_id=user_id, target_name=user.email,
            before={"role": role.name},
        )
        db.commit()
        sync_user_role_cache(db, user_id)
        AuthzService.bump_version()
        return UserRoleService.get_user_roles(db, user_id)

    @staticmethod
    def get_user_roles(db: Session, user_id: int) -> Dict[str, Any]:
        user = UserRoleService.get_user(db, user_id)
        roles = [
            {"id": ur.role.id, "name": ur.role.name}
            for ur in db.query(UserRole).filter(UserRole.user_id == user_id).all() if ur.role
        ]
        return {"user_id": user_id, "email": user.email, "roles": roles}

    @staticmethod
    def effective_permissions(db: Session, user_id: int) -> Dict[str, Any]:
        user = UserRoleService.get_user(db, user_id)
        role_links = db.query(UserRole).filter(UserRole.user_id == user_id).all()
        roles = [ur.role for ur in role_links if ur.role and ur.role.is_active]

        grants: Dict[str, Dict[str, List[str]]] = {m: {a: [] for a in ACTIONS} for m in MODULES}
        for role in roles:
            perms = (
                db.query(Permission)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .filter(RolePermission.role_id == role.id)
                .all()
            )
            for p in perms:
                if p.module in grants and p.action in grants[p.module]:
                    grants[p.module][p.action].append(role.name)

        modules_out = {
            module: {
                action: {"granted": len(via) > 0, "via_roles": via}
                for action, via in actions.items()
            }
            for module, actions in grants.items()
        }
        return {
            "user_id": user_id, "email": user.email,
            "roles": [r.name for r in roles], "modules": modules_out,
        }


# ── Policy engine / authZ contract (SEC-T2) ─────────────────────────────────


class AuthzService:
    """In-process, version-invalidated permission cache. No external cache
    infra exists in this repo yet (checked app/core/ — no Redis client);
    an in-process dict + a global version counter bumped on every
    mutating role/permission/user-role write is enough to hit the ≤50ms
    p95 NFR without adding new infrastructure."""

    _version = 0
    _cache: Dict[int, Tuple[int, float, Dict[str, Set[str]]]] = {}
    _ttl_seconds = settings.RBAC_PERMISSION_CACHE_TTL_SECONDS

    @classmethod
    def bump_version(cls) -> None:
        cls._version += 1

    @classmethod
    def _compute(cls, db: Session, user_id: int) -> Dict[str, Set[str]]:
        role_links = db.query(UserRole).filter(UserRole.user_id == user_id).all()
        perm_set: Dict[str, Set[str]] = {m: set() for m in MODULES}
        for ur in role_links:
            role = ur.role
            if role is None or not role.is_active:
                continue
            perms = (
                db.query(Permission)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .filter(RolePermission.role_id == role.id)
                .all()
            )
            for p in perms:
                perm_set.setdefault(p.module, set()).add(p.action)
        return perm_set

    @classmethod
    def _permission_set(cls, db: Session, user_id: int) -> Dict[str, Set[str]]:
        cached = cls._cache.get(user_id)
        now = time.monotonic()
        if cached is not None:
            version, computed_at, perm_set = cached
            if version == cls._version and (now - computed_at) < cls._ttl_seconds:
                return perm_set
        perm_set = cls._compute(db, user_id)
        cls._cache[user_id] = (cls._version, now, perm_set)
        return perm_set

    @classmethod
    def check(cls, db: Session, user_id: int, module: str, action: str) -> Tuple[bool, str]:
        """Deny-by-default (AC1): only True if the union of the user's
        roles explicitly grants (module, action)."""
        if module not in MODULES:
            return False, f"unknown module '{module}'"
        if action not in ACTIONS:
            return False, f"unknown action '{action}'"
        perm_set = cls._permission_set(db, user_id)
        allowed = action in perm_set.get(module, set())
        reason = "granted" if allowed else "denied (no role grants this action)"
        return allowed, reason


# ── Masking policies (SEC-T3) ────────────────────────────────────────────────


class MaskingPolicyCRUD:
    @staticmethod
    def list_policies(db: Session, connection_id: Optional[int] = None) -> List[MaskingPolicy]:
        q = db.query(MaskingPolicy)
        if connection_id is not None:
            q = q.filter(MaskingPolicy.connection_id == connection_id)
        return q.order_by(MaskingPolicy.id.asc()).all()

    @staticmethod
    def _validate(masking_type: str, exempt_roles: List[str], db: Session) -> None:
        if masking_type not in MASKING_TYPES:
            raise HTTPException(status_code=400, detail=f"masking_type must be one of {MASKING_TYPES}")
        known = {r.name for r in db.query(Role).all()}
        unknown = set(exempt_roles) - known
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown role(s) in exempt_roles: {sorted(unknown)}")

    @staticmethod
    def create_policy(
        db: Session, connection_id: int, table_name: str, column_name: str,
        masking_type: str, exempt_roles: List[str], actor: str,
    ) -> MaskingPolicy:
        MaskingPolicyCRUD._validate(masking_type, exempt_roles, db)
        existing = db.query(MaskingPolicy).filter(
            MaskingPolicy.connection_id == connection_id,
            MaskingPolicy.table_name == table_name,
            MaskingPolicy.column_name == column_name,
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"A masking policy already exists for {table_name}.{column_name}",
            )
        policy = MaskingPolicy(
            connection_id=connection_id, table_name=table_name, column_name=column_name,
            masking_type=masking_type, exempt_roles=exempt_roles,
        )
        db.add(policy)
        db.flush()
        emit_audit_event(
            db, "masking_policy_created", actor=actor, module="security",
            target_type="masking_policy", target_id=policy.id,
            target_name=f"{table_name}.{column_name}",
            after={"masking_type": masking_type, "exempt_roles": exempt_roles},
        )
        db.commit()
        return policy

    @staticmethod
    def update_policy(
        db: Session, policy_id: int, masking_type: Optional[str],
        exempt_roles: Optional[List[str]], actor: str,
    ) -> MaskingPolicy:
        policy = db.query(MaskingPolicy).filter(MaskingPolicy.id == policy_id).first()
        if policy is None:
            raise HTTPException(status_code=404, detail=f"Masking policy {policy_id} not found")
        before = {"masking_type": policy.masking_type, "exempt_roles": policy.exempt_roles}
        MaskingPolicyCRUD._validate(
            masking_type or policy.masking_type, exempt_roles if exempt_roles is not None else policy.exempt_roles, db,
        )
        if masking_type is not None:
            policy.masking_type = masking_type
        if exempt_roles is not None:
            policy.exempt_roles = exempt_roles
        db.flush()
        emit_audit_event(
            db, "masking_policy_updated", actor=actor, module="security",
            target_type="masking_policy", target_id=policy.id,
            target_name=f"{policy.table_name}.{policy.column_name}",
            before=before, after={"masking_type": policy.masking_type, "exempt_roles": policy.exempt_roles},
        )
        db.commit()
        return policy

    @staticmethod
    def delete_policy(db: Session, policy_id: int, actor: str) -> None:
        policy = db.query(MaskingPolicy).filter(MaskingPolicy.id == policy_id).first()
        if policy is None:
            raise HTTPException(status_code=404, detail=f"Masking policy {policy_id} not found")
        emit_audit_event(
            db, "masking_policy_deleted", actor=actor, module="security",
            target_type="masking_policy", target_id=policy.id,
            target_name=f"{policy.table_name}.{policy.column_name}",
            before={"masking_type": policy.masking_type, "exempt_roles": policy.exempt_roles},
        )
        db.delete(policy)
        db.commit()

    @staticmethod
    def apply_masking(value: Any, masking_type: str) -> Any:
        if value is None:
            return None
        if masking_type == "redact":
            return "***"
        if masking_type == "hash":
            digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
            return f"sha256:{digest[:12]}"
        if masking_type == "truncate":
            s = str(value)
            return (s[0] + "***") if s else s
        if masking_type == "substitute":
            return "[MASKED]"
        if masking_type == "nullify":
            return None
        return value


# ── Row-level access filters (SEC-T4) ───────────────────────────────────────


class RowAccessPolicyCRUD:
    @staticmethod
    def list_policies(db: Session, connection_id: Optional[int] = None) -> List[RowAccessPolicy]:
        q = db.query(RowAccessPolicy)
        if connection_id is not None:
            q = q.filter(RowAccessPolicy.connection_id == connection_id)
        return q.order_by(RowAccessPolicy.id.asc()).all()

    @staticmethod
    def _validate(filter_conditions: List[Dict[str, Any]], applies_to_roles: List[str], db: Session) -> None:
        if not filter_conditions:
            raise HTTPException(status_code=400, detail="filter_conditions must have at least one condition")
        for cond in filter_conditions:
            missing = {"field", "operator", "value"} - set(cond.keys())
            if missing:
                raise HTTPException(status_code=400, detail=f"filter condition missing field(s): {sorted(missing)}")
            if cond["operator"] not in _FILTER_OPERATORS:
                raise HTTPException(status_code=400, detail=f"operator must be one of {_FILTER_OPERATORS}")
            if cond.get("logic") and cond["logic"] not in ("AND", "OR"):
                raise HTTPException(status_code=400, detail="logic must be 'AND' or 'OR'")
        known = {r.name for r in db.query(Role).all()}
        unknown = set(applies_to_roles) - known
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown role(s) in applies_to_roles: {sorted(unknown)}")

    @staticmethod
    def create_policy(
        db: Session, connection_id: int, table_name: str,
        filter_conditions: List[Dict[str, Any]], applies_to_roles: List[str], actor: str,
    ) -> RowAccessPolicy:
        RowAccessPolicyCRUD._validate(filter_conditions, applies_to_roles, db)
        policy = RowAccessPolicy(
            connection_id=connection_id, table_name=table_name,
            filter_conditions=filter_conditions, applies_to_roles=applies_to_roles,
        )
        db.add(policy)
        db.flush()
        emit_audit_event(
            db, "row_access_policy_created", actor=actor, module="security",
            target_type="row_access_policy", target_id=policy.id, target_name=table_name,
            after={"filter_conditions": filter_conditions, "applies_to_roles": applies_to_roles},
        )
        db.commit()
        return policy

    @staticmethod
    def update_policy(
        db: Session, policy_id: int, filter_conditions: Optional[List[Dict[str, Any]]],
        applies_to_roles: Optional[List[str]], actor: str,
    ) -> RowAccessPolicy:
        policy = db.query(RowAccessPolicy).filter(RowAccessPolicy.id == policy_id).first()
        if policy is None:
            raise HTTPException(status_code=404, detail=f"Row access policy {policy_id} not found")
        before = {"filter_conditions": policy.filter_conditions, "applies_to_roles": policy.applies_to_roles}
        RowAccessPolicyCRUD._validate(
            filter_conditions if filter_conditions is not None else policy.filter_conditions,
            applies_to_roles if applies_to_roles is not None else policy.applies_to_roles,
            db,
        )
        if filter_conditions is not None:
            policy.filter_conditions = filter_conditions
        if applies_to_roles is not None:
            policy.applies_to_roles = applies_to_roles
        db.flush()
        emit_audit_event(
            db, "row_access_policy_updated", actor=actor, module="security",
            target_type="row_access_policy", target_id=policy.id, target_name=policy.table_name,
            before=before, after={"filter_conditions": policy.filter_conditions, "applies_to_roles": policy.applies_to_roles},
        )
        db.commit()
        return policy

    @staticmethod
    def delete_policy(db: Session, policy_id: int, actor: str) -> None:
        policy = db.query(RowAccessPolicy).filter(RowAccessPolicy.id == policy_id).first()
        if policy is None:
            raise HTTPException(status_code=404, detail=f"Row access policy {policy_id} not found")
        emit_audit_event(
            db, "row_access_policy_deleted", actor=actor, module="security",
            target_type="row_access_policy", target_id=policy.id, target_name=policy.table_name,
            before={"filter_conditions": policy.filter_conditions, "applies_to_roles": policy.applies_to_roles},
        )
        db.delete(policy)
        db.commit()
