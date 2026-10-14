import hashlib

import pytest

from app.core.database_runtime import resolve_database_runtime
from app.core.runtime_config import configure_databricks_runtime_environment


def test_databricks_runtime_derives_stable_secret_without_customer_secret():
    env = {
        "DATABRICKS_APP_PORT": "8080",
        "DATABRICKS_APP_NAME": "dataone-test",
        "DATABRICKS_CLIENT_ID": "client-id",
        "DATABRICKS_CLIENT_SECRET": "platform-secret",
    }

    configure_databricks_runtime_environment(env)

    expected = hashlib.sha256(
        b"dataone-jwt-v1\0dataone-test\0client-id\0platform-secret"
    ).hexdigest()
    assert env["SECRET_KEY"] == expected
    assert env["DATABRICKS_NATIVE_MODE"] == "true"


def test_databricks_runtime_requires_injected_app_identity():
    with pytest.raises(RuntimeError, match="DATABRICKS_CLIENT_ID"):
        configure_databricks_runtime_environment({"DATABRICKS_APP_PORT": "8080"})


def test_lakebase_is_selected_when_complete_resource_is_injected():
    env = {
        "DATABRICKS_APP_PORT": "8080",
        "PGHOST": "example.database.cloud.databricks.com",
        "PGPORT": "5432",
        "PGDATABASE": "dataone",
        "PGUSER": "app-client-id",
        "LAKEBASE_ENDPOINT": "projects/p/branches/b/endpoints/e",
    }
    runtime = resolve_database_runtime("postgresql://local", None, env)
    assert runtime.mode == "lakebase"
    assert runtime.persistent is True


def test_partial_lakebase_resource_fails_explicitly():
    with pytest.raises(RuntimeError, match="PGPORT"):
        resolve_database_runtime(
            "postgresql://local",
            None,
            {"DATABRICKS_APP_PORT": "8080", "PGHOST": "db"},
        )


def test_databricks_app_without_lakebase_uses_visible_setup_mode():
    runtime = resolve_database_runtime(
        "postgresql://must-not-be-used",
        None,
        {"DATABRICKS_APP_PORT": "8080", "DATABRICKS_APP_NAME": "dataone 123"},
    )
    assert runtime.mode == "temporary_setup"
    assert runtime.url == "sqlite:////tmp/dataone-123-setup.db"
    assert runtime.persistent is False


def test_explicit_external_database_remains_supported():
    runtime = resolve_database_runtime(
        "postgresql://local",
        "postgresql://managed/dataone",
        {"DATABRICKS_APP_PORT": "8080"},
    )
    assert runtime.mode == "external"
    assert runtime.url == "postgresql://managed/dataone"
