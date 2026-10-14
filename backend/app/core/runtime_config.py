"""Databricks Apps runtime configuration that must run before Settings loads."""

from __future__ import annotations

import hashlib
import os
from collections.abc import MutableMapping


def configure_databricks_runtime_environment(
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Populate safe derived settings from the Databricks-injected app identity.

    Databricks creates a unique service principal for each installed app and
    injects its OAuth client credentials. Deriving the JWT signing key keeps
    DataOne installation-free without persisting or logging an additional
    customer-managed secret. A service-principal secret rotation deliberately
    invalidates existing DataOne sessions.
    """

    env = environ if environ is not None else os.environ
    if not env.get("DATABRICKS_APP_PORT"):
        return

    env["DATABRICKS_NATIVE_MODE"] = "true"
    if env.get("SECRET_KEY"):
        return

    client_id = env.get("DATABRICKS_CLIENT_ID")
    client_secret = env.get("DATABRICKS_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Databricks did not inject DATABRICKS_CLIENT_ID and "
            "DATABRICKS_CLIENT_SECRET for the app service principal."
        )

    app_name = env.get("DATABRICKS_APP_NAME", "dataone")
    material = f"dataone-jwt-v1\0{app_name}\0{client_id}\0{client_secret}".encode()
    env["SECRET_KEY"] = hashlib.sha256(material).hexdigest()

