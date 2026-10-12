from pydantic import BaseModel, Field
from typing import Any, Dict, List, Literal, Optional
from datetime import datetime


class ConnectionBase(BaseModel):
    name: str
    type: str
    config: Dict[str, Any]
    environment: Literal["dev", "qa", "uat", "prod"] = "dev"


class ConnectionEnvironmentUpdate(BaseModel):
    environment: Literal["dev", "qa", "uat", "prod"]


class ConnectionCreate(ConnectionBase):
    pass


class ConnectionResponse(ConnectionBase):
    """Client-facing connection. `config` MUST be redacted via
    connector_catalog.redact_config before constructing this (TRD FR3) —
    endpoints build it through the router's serializer, never straight
    from the ORM object."""
    id: int
    health_status: str = "unknown"
    last_tested_at: Optional[datetime] = None
    last_test_error: Optional[str] = None
    is_deleted: bool = False
    created_by: Optional[str] = None
    owner_email: Optional[str] = None
    created_at: Optional[datetime] = None
    databricks_detected: bool = False  # True for auto-detected workspace connection

    class Config:
        from_attributes = True


# --- Connector type catalog (connector_tasks #3, FR1) ---

class FieldDef(BaseModel):
    key: str
    label: str
    type: str                # "text", "number", "password", "select", "textarea", "boolean"
    required: bool = False
    default: Any = None
    placeholder: str = ""
    options: List[str] = Field(default_factory=list)  # for "select" type
    secret: bool = False     # if True, value is never returned on GET


class ConnectorTypeMetadata(BaseModel):
    name: str
    type: str
    category: str            # "relational", "file", "warehouse", "object_store"
    icon: str
    description: str
    fields: List[FieldDef]
    secret_fields: List[str]


# --- Test connection diagnostics (connector_tasks #4, FR4) ---

class TestDiagnostics(BaseModel):
    reachable: bool = False
    authenticated: bool = False
    database_accessible: bool = False
    version: Optional[str] = None
    latency_ms: Optional[int] = None


class TestErrorDetail(BaseModel):
    code: str
    message: str


class TestConnectionResponse(BaseModel):
    id: int
    name: str
    status: str              # "connected" | "failed" — shape kept for existing consumers
    diagnostics: TestDiagnostics
    error: Optional[TestErrorDetail] = None


# --- Health summary (connector_tasks #5, FR5) ---

class HealthSummary(BaseModel):
    total: int
    healthy: int
    degraded: int
    down: int
    unknown: int
    last_tested_at: Optional[datetime] = None
