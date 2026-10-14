"""Resolve DataOne application-state storage for each deployment mode."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from collections.abc import Mapping


@dataclass(frozen=True)
class DatabaseRuntime:
    mode: str
    url: str
    persistent: bool


def _is_databricks_app(env: Mapping[str, str]) -> bool:
    return bool(env.get("DATABRICKS_APP_PORT")) or env.get("DATABRICKS_NATIVE_MODE", "").lower() == "true"


def resolve_database_runtime(
    configured_database_url: str,
    app_database_url: str | None,
    environ: Mapping[str, str] | None = None,
) -> DatabaseRuntime:
    """Choose explicit external DB, Lakebase, temporary setup DB, or local DB."""

    env = environ if environ is not None else os.environ
    if app_database_url:
        return DatabaseRuntime("external", app_database_url, True)

    lakebase_fields = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "LAKEBASE_ENDPOINT")
    available = [name for name in lakebase_fields if env.get(name)]
    if available:
        missing = [name for name in lakebase_fields if not env.get(name)]
        if missing:
            raise RuntimeError(
                "Incomplete Lakebase app resource configuration; missing: " + ", ".join(missing)
            )
        # The SQLAlchemy URL is assembled in database.py so the short-lived
        # OAuth database token is never embedded in a string or log record.
        return DatabaseRuntime("lakebase", "lakebase://injected", True)

    if _is_databricks_app(env):
        app_name = re.sub(r"[^A-Za-z0-9_.-]", "-", env.get("DATABRICKS_APP_NAME", "dataone"))
        return DatabaseRuntime("temporary_setup", f"sqlite:////tmp/{app_name}-setup.db", False)

    return DatabaseRuntime("configured", configured_database_url, True)

