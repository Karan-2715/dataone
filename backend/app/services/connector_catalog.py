"""Connector type catalog (connector_tasks #3, TRD FR1).

Single source of truth for supported connector types: display metadata,
per-type field definitions (drives dynamic forms on the frontend), which
fields are secret, and boundary validation of submitted configs.
"""
import logging
import re
from typing import Any, Dict

from fastapi import HTTPException

from app.schemas.connection import ConnectorTypeMetadata, FieldDef

logger = logging.getLogger(__name__)

# Value substituted for secret fields in every API response. The raw value
# never leaves the service layer (TRD FR3).
REDACTED = "***"

# Secret-ish keys masked even for unknown/legacy types where no catalog
# entry can tell us which fields are secret.
_FALLBACK_SECRET_KEYS = {"password", "secret", "token", "api_key", "apikey",
                         "connection_properties", "passwd"}

# user:password@ credentials embedded in DSN-style URLs.
_URL_CREDS_RE = re.compile(r"(://[^:/@\s]+:)[^@\s]+(@)")

CONNECTOR_TYPES: Dict[str, ConnectorTypeMetadata] = {
    "postgres": ConnectorTypeMetadata(
        name="PostgreSQL",
        type="postgres",
        category="relational",
        icon="postgresql",
        description="PostgreSQL relational database",
        fields=[
            FieldDef(key="host", label="Host", type="text", required=True, placeholder="localhost"),
            FieldDef(key="port", label="Port", type="number", required=True, default=5432),
            FieldDef(key="dbname", label="Database Name", type="text", required=True),
            FieldDef(key="user", label="Username", type="text", required=True),
            FieldDef(key="password", label="Password", type="password", required=True, secret=True),
            FieldDef(key="catalog", label="Databricks Catalog (Optional)", type="text", required=False, placeholder="e.g. postgres_catalog"),
        ],
        secret_fields=["password"],
    ),
    "mysql": ConnectorTypeMetadata(
        name="MySQL",
        type="mysql",
        category="relational",
        icon="mysql",
        description="MySQL / MariaDB relational database",
        fields=[
            FieldDef(key="host", label="Host", type="text", required=True, placeholder="localhost"),
            FieldDef(key="port", label="Port", type="number", required=True, default=3306),
            FieldDef(key="dbname", label="Database Name", type="text", required=True),
            FieldDef(key="user", label="Username", type="text", required=True),
            FieldDef(key="password", label="Password", type="password", required=True, secret=True),
            FieldDef(key="catalog", label="Databricks Catalog (Optional)", type="text", required=False, placeholder="e.g. saloni_rds_catalog"),
        ],
        secret_fields=["password"],
    ),
    "oracle": ConnectorTypeMetadata(
        name="Oracle",
        type="oracle",
        category="relational",
        icon="oracle",
        description="Oracle Database",
        fields=[
            FieldDef(key="host", label="Host", type="text", required=True),
            FieldDef(key="port", label="Port", type="number", required=True, default=1521),
            FieldDef(key="service_name", label="Service Name", type="text", required=True),
            FieldDef(key="user", label="Username", type="text", required=True),
            FieldDef(key="password", label="Password", type="password", required=True, secret=True),
        ],
        secret_fields=["password"],
    ),
    "sqlite": ConnectorTypeMetadata(
        name="SQLite",
        type="sqlite",
        category="file",
        icon="sqlite",
        description="SQLite file-based database",
        fields=[
            FieldDef(key="path", label="File Path", type="text", required=True,
                     placeholder="/data/mydb.sqlite"),
        ],
        secret_fields=[],
    ),
    "jdbc": ConnectorTypeMetadata(
        name="Generic JDBC",
        type="jdbc",
        category="relational",
        icon="database",
        description="Generic SQLAlchemy/JDBC-compliant database (dialect URL)",
        fields=[
            FieldDef(key="url", label="Connection URL", type="text", required=True,
                     placeholder="postgresql://user:pass@host:5432/db", secret=True),
            FieldDef(key="schema", label="Schema", type="text", required=False),
        ],
        # The URL can embed credentials; treated as secret and shown with
        # the password portion masked (see redact_config).
        secret_fields=["url"],
    ),
    "databricks": ConnectorTypeMetadata(
        name="Databricks",
        type="databricks",
        category="warehouse",
        icon="databricks",
        description="Databricks SQL Warehouse with Unity Catalog support",
        fields=[
            FieldDef(
                key="server_hostname", 
                label="Server Hostname", 
                type="text", 
                required=True,
                placeholder="adb-1234567890123456.7.azuredatabricks.net",
                help_text="Workspace URL (find in SQL Warehouse → Connection Details)"
            ),
            FieldDef(
                key="http_path", 
                label="HTTP Path", 
                type="text", 
                required=True,
                placeholder="/sql/1.0/warehouses/abc123def456",
                help_text="SQL Warehouse HTTP path (find in Connection Details)"
            ),
            FieldDef(
                key="access_token", 
                label="Access Token", 
                type="password", 
                required=True,
                secret=True,
                help_text="Personal access token or OAuth token"
            ),
            FieldDef(
                key="catalog", 
                label="Catalog", 
                type="text", 
                required=False,
                default="main",
                placeholder="main",
                help_text="Unity Catalog name (optional, defaults to 'main')"
            ),
            FieldDef(
                key="schema", 
                label="Schema", 
                type="text", 
                required=False,
                default="default",
                placeholder="default",
                help_text="Schema/Database name (optional, defaults to 'default')"
            ),
        ],
        secret_fields=["access_token"],
    ),
}


def get_type_or_404(conn_type: str) -> ConnectorTypeMetadata:
    meta = CONNECTOR_TYPES.get(conn_type)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown connector type '{conn_type}'. Valid: {sorted(CONNECTOR_TYPES)}",
        )
    return meta


def secret_fields_for_type(conn_type: str) -> set:
    meta = CONNECTOR_TYPES.get(conn_type)
    return set(meta.secret_fields) if meta else set(_FALLBACK_SECRET_KEYS)


def validate_config(conn_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Boundary validation of a submitted config against the type's fields.

    Returns a cleaned copy: unknown keys stripped (forward-compatible),
    required fields present and non-empty, number fields numeric, select
    fields limited to their options. Raises 422 with a per-field message.
    """
    if conn_type not in CONNECTOR_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported connector type '{conn_type}'. Valid: {sorted(CONNECTOR_TYPES)}",
        )
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="config must be a JSON object")

    meta = CONNECTOR_TYPES[conn_type]
    fields_by_key = {f.key: f for f in meta.fields}

    unknown = sorted(set(config) - set(fields_by_key))
    if unknown:
        logger.info("[connectors] stripping unknown config fields for type=%s: %s",
                    conn_type, unknown)

    cleaned: Dict[str, Any] = {}
    errors = []
    for key, field in fields_by_key.items():
        value = config.get(key)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            if field.required:
                errors.append(f"'{key}' is required for type '{conn_type}'")
            elif field.default is not None:
                cleaned[key] = field.default
            continue
        if field.type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    errors.append(f"'{key}' must be a number")
                    continue
        if field.type == "select" and field.options and value not in field.options:
            errors.append(f"'{key}' must be one of {field.options}")
            continue
        cleaned[key] = value

    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    return cleaned


def redact_config(conn_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Copy of `config` safe to return to a client: secret fields masked,
    URL-embedded credentials masked. Applied to every API response (FR3)."""
    secret_keys = secret_fields_for_type(conn_type) | _FALLBACK_SECRET_KEYS
    redacted: Dict[str, Any] = {}
    for key, value in (config or {}).items():
        if key in secret_keys:
            if isinstance(value, str) and "://" in value:
                redacted[key] = _URL_CREDS_RE.sub(rf"\g<1>{REDACTED}\g<2>", value)
            else:
                redacted[key] = REDACTED
        else:
            redacted[key] = value
    return redacted
