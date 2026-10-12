"""RBAC + data-protection-policy models (DP-SEC-001, SEC-T1/T3/T4).

Role/Permission/RolePermission/UserRole are a standard normalized RBAC
shape. ``User.role`` (app/models/user.py) is kept as a denormalized cache
column so every existing ``require_role()`` call site keeps working
unchanged — it's synced from a user's assigned roles by
``rbac_service.sync_user_role_cache`` on every UserRole write, picking the
highest-privilege canonical role (admin > analyst > viewer) as the cached
value. UserRole/RolePermission are the source of truth for the new
fine-grained ``authz_check`` contract; User.role is a read-optimization,
not a second source of truth.
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint,
)
from sqlalchemy.orm import relationship, backref
from sqlalchemy.sql import func
from app.core.database import Base


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    role_permissions = relationship("RolePermission", backref="role", cascade="all, delete-orphan")
    user_roles = relationship("UserRole", backref="role", cascade="all, delete-orphan")


class Permission(Base):
    """Static catalog of (module, action) pairs — seeded once, never user-created."""
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("module", "action", name="uq_permission_module_action"),)

    id = Column(Integer, primary_key=True, index=True)
    module = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)

    role_permissions = relationship("RolePermission", backref="permission", cascade="all, delete-orphan")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),)

    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    permission_id = Column(Integer, ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False, index=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", backref=backref("user_roles", cascade="all, delete-orphan"))


MASKING_TYPES = ("redact", "hash", "truncate", "substitute", "nullify")


class MaskingPolicy(Base):
    """Column-level PII masking policy (SEC-T3, FR4). ``exempt_roles`` lists
    role names that see the real value; every other role sees the masked
    value. Enforced by app.services.viz_service against connection_id +
    table_name + column_name — see that module for the enforcement point."""
    __tablename__ = "masking_policies"
    __table_args__ = (
        UniqueConstraint("connection_id", "table_name", "column_name", name="uq_masking_policy_target"),
    )

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="CASCADE"), nullable=False, index=True)
    table_name = Column(String, nullable=False)
    column_name = Column(String, nullable=False)
    masking_type = Column(String, nullable=False)
    exempt_roles = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class RowAccessPolicy(Base):
    """Row-level access filter (SEC-T4, FR5). ``filter_conditions`` is a list
    of {field, operator, value, logic} dicts ANDed/ORed per each entry's
    ``logic`` field (first entry's logic is ignored). ``applies_to_roles``
    lists role names this filter is applied to; roles not listed see
    unfiltered rows. Enforced by app.services.viz_service."""
    __tablename__ = "row_access_policies"

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="CASCADE"), nullable=False, index=True)
    table_name = Column(String, nullable=False)
    filter_conditions = Column(JSON, nullable=False, default=list)
    applies_to_roles = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
