from pydantic import field_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "DataOne"
    DATABASE_URL: str = "postgresql://postgres:postgres@postgres:5432/dataone"
    APP_DATABASE_URL: str | None = None
    
    # URLs for OAuth callbacks
    BACKEND_URL: str = "http://localhost:8011"
    FRONTEND_URL: str = "http://localhost:3011"
    
    OLLAMA_HOST: str = "http://host.docker.internal:11434"
    OLLAMA_MODEL: str = "llama3"
    OLLAMA_TIMEOUT: int = 15
    OLLAMA_MAX_RETRIES: int = 2
    CELERY_BROKER_URL: str = "redis://broker:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://broker:6379/0"
    LOG_LEVEL: str = "INFO"
    SCHEMA_DRIFT_INTERVAL_MINUTES: int = 60
    SECRET_KEY: str = "change-me-in-production-use-a-long-random-string"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    ADMIN_DEFAULT_PASSWORD: str = "admin123"
    # Dashboard aggregation cache (dashboard_tasks #2). TTL <= 0 disables caching.
    DASHBOARD_CACHE_TTL: int = 30
    DASHBOARD_CACHE_MAXSIZE: int = 256
    # Risk register aggregation performs live schema reads for mapped pairs.
    # TTL <= 0 disables its per-process cache (useful for tests/debugging).
    RISK_REGISTER_CACHE_TTL: int = 30
    RISK_REGISTER_CACHE_MAXSIZE: int = 256
    # Connectors (connector_tasks #4/#5): hard cap on test-connection time
    # (TRD perf NFR: ≤5s or clear timeout) and health-check cadence/throttle.
    CONNECTOR_TEST_TIMEOUT_SECONDS: int = 5
    HEALTH_CHECK_INTERVAL_MINUTES: int = 5
    HEALTH_CHECK_RATE_LIMIT: str = "10/m"
    # AI Autopilot governance (ai_autopilot_tasks #2/#5/#7).
    AUTOPILOT_TYPE_AUTO_LIMIT_PER_HOUR: int = 10
    AUTOPILOT_GLOBAL_AUTO_LIMIT_PER_HOUR: int = 20
    AUTOPILOT_EVALUATE_INTERVAL_MINUTES: int = 2
    AUTOPILOT_BREAKER_THRESHOLD: int = 3
    AUTOPILOT_BREAKER_WINDOW_MINUTES: int = 60
    AUTOPILOT_DRIFT_LOOKBACK_HOURS: int = 24
    # Audit Trail (audit_trail_tasks #2/#3/#6).
    AUDIT_INGEST_BATCH_MAX: int = 100
    AUDIT_DB_WRITE_MAX_RETRIES: int = 2
    AUDIT_DB_CIRCUIT_FAILURE_THRESHOLD: int = 3
    AUDIT_DB_CIRCUIT_RESET_TIMEOUT_SECONDS: int = 15
    AUDIT_BUFFER_MAX_SIZE: int = 5000
    AUDIT_BUFFER_FLUSH_INTERVAL_MINUTES: int = 1
    AUDIT_EXPORT_MAX_ROWS: int = 100000
    AUDIT_RETENTION_DAYS: int = 90
    # Query Studio (query_studio_tasks #1/#3/#5).
    QUERY_STUDIO_MAX_RESULT_ROWS: int = 5000
    QUERY_STUDIO_EXECUTION_TIMEOUT_SECONDS: int = 30
    QUERY_STUDIO_DEFAULT_PAGE_SIZE: int = 100
    # Schema Intel profiling (schema_intel_tasks #2, PII sign-off #8 decisions 2/4).
    SCHEMA_INTEL_SAMPLE_LIMIT: int = 1000
    SCHEMA_INTEL_MAX_DISTINCT_SCAN_ROWS: int = 100000
    SCHEMA_INTEL_USE_SEPARATE_CREDENTIALS: bool = False
    # Profiling enrichment bounds (agentic_dba_tasks #2) — FK-candidate
    # inference compares only against declared PK columns, capped:
    SCHEMA_INTEL_FK_MAX_TABLES: int = 25
    SCHEMA_INTEL_FK_PK_VALUE_LIMIT: int = 10000
    SCHEMA_INTEL_FK_MIN_OVERLAP: float = 0.5

    RBAC_PERMISSION_CACHE_TTL_SECONDS: int = 30

    # Databricks data-quality ingest (POST /api/v1/data-quality/ingest). No
    # checked-in fallback — unset disables the endpoint.
    DQ_INGEST_KEY: str | None = None

    # How long a Databricks connection's cached Unity Catalog schema is served before the mapper rescans it.
    DATABRICKS_SCHEMA_TTL_SECONDS: int = 600

    # Genie chat (NL -> SQL): max result rows returned to the browser per answer
    GENIE_CHAT_MAX_ROWS: int = 500

    # Databricks file data-quality pipeline (databricks/file_quality bundle). Override per workspace via env.
    DQ_FILES_VOLUME_PATH: str = "/Volumes/workspace/dataone_dq/uploads"
    DQ_FILES_RESULTS_TABLE: str = "workspace.dataone_dq.file_quality_results"
    DQ_FILES_JOB_NAME: str = "dataone-file-quality-job"

    # Agentic DBA Copilot (agentic_dba_tasks #3) — sanity caps on plan size
    # (design decision #11) and an LLM-adaptation kill switch.
    AGENTIC_DBA_MAX_TABLES: int = 10
    AGENTIC_DBA_MAX_COLUMNS_PER_TABLE: int = 30
    AGENTIC_DBA_LLM_ENABLED: bool = True

    # ACI.dev external tool-calling integration (aci_integration_tasks).
    # ACI_API_KEY deliberately has no checked-in fallback — unset means the
    # integration is disabled and every ACI call fails with a clear
    # "not configured" error instead of a mystery auth failure.
    ACI_BASE_URL: str = "http://aci:8000"
    ACI_API_KEY: str | None = None
    ACI_PORTAL_URL: str = "http://localhost:3001"
    ACI_LINKED_ACCOUNT_OWNER_ID: str = "dataone"
    ACI_TIMEOUT: int = 10
    ACI_MAX_RETRIES: int = 2
    # Fixed, admin-configured destination for the ONLY auto-capable external
    # action (notify_slack_internal). Never user/LLM-suppliable at request
    # time — the destination is part of the risk (aci tasks #3).
    ACI_SLACK_INTERNAL_CHANNEL: str = ""
    # Base URL used in notify-out links back to DataOne's own approval UI.
    DATAONE_BASE_URL: str = "http://localhost:3011"

    # Connector credential vaulting (keeperdb_integration_tasks; resolves
    # connector_tasks #2's blocked decision — repo owner chose BOTH backends,
    # aes256 default, 2026-07-14). Unset key/config = legacy mode: secrets
    # stay in the config column, responses stay redacted, nothing breaks.
    SECRET_MANAGER_BACKEND: str = "aes256"  # "aes256" | "keeper"
    # base64-encoded 32-byte key; generate: openssl rand -base64 32
    SECRETS_ENCRYPTION_KEY: str | None = None
    # Set only during a key-rotation window so old rows stay readable.
    SECRETS_ENCRYPTION_KEY_PREVIOUS: str | None = None
    # Keeper Secrets Manager: path to the config file produced by the
    # one-time-token bootstrap. A mounted file — NEVER a literal token.
    KSM_CONFIG_PATH: str | None = None
    KSM_FOLDER_UID: str = ""

    # Microsoft Entra SSO (stage stopgap — reuses the OneStore App
    # Registration). Unset ENTRA_CLIENT_ID means the integration is
    # disabled and /entra/login returns a clear "not configured" error.
    ENTRA_CLIENT_ID: str | None = None
    ENTRA_TENANT_ID: str | None = None
    ENTRA_CLIENT_SECRET: str | None = None
    ENTRA_REDIRECT_URI: str | None = None
    FRONTEND_LOGIN_URL: str | None = None
    # Temporary stopgap: when set (e.g. "veltris.com"), any Entra login whose
    # email domain matches is auto-provisioned as admin if no DataOne account
    # exists yet. Unset to go back to matching pre-existing accounts only —
    # this is meant to be turned off, not a permanent provisioning policy.
    ENTRA_AUTO_PROVISION_DOMAIN: str | None = None

    # Databricks integration settings (Phase 1: Integration)
    # Optional - only needed when using Databricks LLM or Unity Catalog features
    DATABRICKS_WORKSPACE_URL: str | None = None
    DATABRICKS_ACCESS_TOKEN: str | None = None
    DATABRICKS_LLM_ENDPOINT: str | None = None  # Model Serving endpoint name
    DATABRICKS_USE_LLM: bool = False  # Enable Databricks LLM instead of Ollama
    DATABRICKS_GENIE_SPACE_ID: str | None = None  # For Genie SQL generation
    
    # Databricks Lakehouse App settings (Phase 2: Marketplace)
    # Enabled when running as a native Databricks app with OAuth
    DATABRICKS_NATIVE_MODE: bool = False  # True = OAuth mode, False = PAT mode
    DATABRICKS_HOST: str | None = None  # Databricks host (e.g., dbc-xxx.cloud.databricks.com)
    DATABRICKS_OAUTH_CLIENT_ID: str | None = None  # OAuth client ID
    DATABRICKS_OAUTH_CLIENT_SECRET: str | None = None  # OAuth client secret (omit for a public PKCE client)
    # Scopes requested at sign-in. The workspace installer creates jobs, secrets, volumes and uploads
    # notebooks as the user, so it needs all-apis; offline_access yields a refresh token.
    DATABRICKS_OAUTH_SCOPES: str = "all-apis offline_access"
    ENABLE_UNITY_CATALOG: bool = True  # Enable Unity Catalog delegation
    ENABLE_EXTERNAL_CONNECTORS: bool = True  # Allow non-Databricks connections (multi-source)

    @field_validator("SECRET_MANAGER_BACKEND")
    @classmethod
    def _validate_secret_backend(cls, v: str) -> str:
        # Fail fast at boot on a typo'd backend name rather than silently
        # storing connector credentials in plaintext at runtime.
        allowed = {"aes256", "keeper"}
        if v not in allowed:
            raise ValueError(
                f"SECRET_MANAGER_BACKEND must be one of {sorted(allowed)}, got '{v}'")
        return v

    class Config:
        env_file = ".env"

settings = Settings()
