"""Pydantic schemas for the Security Admin API (DP-SEC-001)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.rbac_service import ACTIONS, MODULES
from app.models.security import MASKING_TYPES

_FILTER_OPERATORS = ("=", "!=", ">", "<", ">=", "<=", "in", "not in")


# ── Roles / Permissions ──────────────────────────────────────────────────


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    is_active: Optional[bool] = None


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    is_active: bool
    permission_count: int
    user_count: int
    created_at: datetime
    updated_at: datetime


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    module: str
    action: str


class RolePermissionSetRequest(BaseModel):
    permission_ids: List[int] = Field(default_factory=list)


class RolePermissionMatrixEntry(BaseModel):
    role: RoleRead
    permission_ids: List[int]


# ── Users / role assignment ──────────────────────────────────────────────


class UserSummary(BaseModel):
    id: int
    email: str
    cached_role: str
    is_active: bool
    roles: List[str]


class UserRoleAssignRequest(BaseModel):
    role_id: int = Field(..., ge=1)


class UserRolesResponse(BaseModel):
    user_id: int
    email: str
    roles: List[Dict[str, Any]]


class EffectivePermissionAction(BaseModel):
    granted: bool
    via_roles: List[str]


class EffectivePermissionsResponse(BaseModel):
    user_id: int
    email: str
    roles: List[str]
    modules: Dict[str, Dict[str, EffectivePermissionAction]]


# ── AuthZ check contract ─────────────────────────────────────────────────


class AuthzCheckRequest(BaseModel):
    module: str
    action: str

    @field_validator("module")
    @classmethod
    def _valid_module(cls, v: str) -> str:
        if v not in MODULES:
            raise ValueError(f"module must be one of {MODULES}")
        return v

    @field_validator("action")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        if v not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}")
        return v


class AuthzCheckResponse(BaseModel):
    allowed: bool
    reason: str
    module: str
    action: str


# ── Masking policies ──────────────────────────────────────────────────────


class MaskingPolicyCreate(BaseModel):
    connection_id: int = Field(..., ge=1)
    table_name: str = Field(..., min_length=1, max_length=200)
    column_name: str = Field(..., min_length=1, max_length=200)
    masking_type: str
    exempt_roles: List[str] = Field(default_factory=list)

    @field_validator("masking_type")
    @classmethod
    def _valid_masking_type(cls, v: str) -> str:
        if v not in MASKING_TYPES:
            raise ValueError(f"masking_type must be one of {MASKING_TYPES}")
        return v


class MaskingPolicyUpdate(BaseModel):
    masking_type: Optional[str] = None
    exempt_roles: Optional[List[str]] = None

    @field_validator("masking_type")
    @classmethod
    def _valid_masking_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in MASKING_TYPES:
            raise ValueError(f"masking_type must be one of {MASKING_TYPES}")
        return v


class MaskingPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connection_id: int
    table_name: str
    column_name: str
    masking_type: str
    exempt_roles: List[str]
    created_at: datetime
    updated_at: datetime


# ── Row access policies ──────────────────────────────────────────────────


class FilterCondition(BaseModel):
    field: str
    operator: str
    value: Any
    logic: Optional[str] = None

    @field_validator("operator")
    @classmethod
    def _valid_operator(cls, v: str) -> str:
        if v not in _FILTER_OPERATORS:
            raise ValueError(f"operator must be one of {_FILTER_OPERATORS}")
        return v

    @field_validator("logic")
    @classmethod
    def _valid_logic(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("AND", "OR"):
            raise ValueError("logic must be 'AND' or 'OR'")
        return v


class RowAccessPolicyCreate(BaseModel):
    connection_id: int = Field(..., ge=1)
    table_name: str = Field(..., min_length=1, max_length=200)
    filter_conditions: List[FilterCondition] = Field(..., min_length=1)
    applies_to_roles: List[str] = Field(default_factory=list)


class RowAccessPolicyUpdate(BaseModel):
    filter_conditions: Optional[List[FilterCondition]] = None
    applies_to_roles: Optional[List[str]] = None


class RowAccessPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connection_id: int
    table_name: str
    filter_conditions: List[Dict[str, Any]]
    applies_to_roles: List[str]
    created_at: datetime
    updated_at: datetime
