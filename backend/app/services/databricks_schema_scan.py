"""Fast Unity Catalog schema scan over the Databricks REST API (catalogs -> schemas -> tables with columns).

Used by ``GET /connectors/{id}/schema`` for Databricks connections. The previous path reused another connection's
cached snapshot (so a new user saw a stale, foreign catalog list) or fell back to a slow per-schema Spark scan that
fails when the user's forwarded token lacks SQL scopes. This runs as the app's workspace client, like the Quick
Upload and catalog pickers, so it shows what the DataOne app can actually reach in the current workspace.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

SKIP_CATALOGS = {"system", "__databricks_internal"}
SKIP_SCHEMAS = {"information_schema", "__databricks_internal"}
MAX_WORKERS = 8


def _columns(table) -> List[Dict[str, Any]]:
    cols = sorted(table.columns or [], key=lambda c: c.position if c.position is not None else 0)
    return [
        {
            "name": c.name,
            "type": c.type_text or (c.type_name.value if c.type_name else ""),
            "nullable": True if c.nullable is None else bool(c.nullable),
            "primary_key": False,
            "foreign_keys": [],
            "ordinal_position": i + 1,
            "default": None,
        }
        for i, c in enumerate(cols)
    ]


def _scan_schema(wc, catalog: str, schema: str) -> Dict[str, List[Dict[str, Any]]]:
    try:
        tables = list(wc.tables.list(catalog_name=catalog, schema_name=schema))
    except Exception as exc:  # no access / foreign catalog hiccup: skip this schema, keep the rest
        logger.warning("[schema_scan] could not list tables in %s.%s: %s", catalog, schema, exc)
        return {}
    if not tables:
        return {f"{catalog}.{schema}.(empty_schema)": []}  # lets the UI show the schema itself
    return {f"{catalog}.{schema}.{t.name}": _columns(t) for t in tables if t.name}


def scan_unity_catalog(wc) -> Dict[str, List[Dict[str, Any]]]:
    """``{"catalog.schema.table": [column, ...]}`` for every catalog the client can see."""
    targets: List[Tuple[str, str]] = []
    for cat in wc.catalogs.list():
        if not cat.name or cat.name.lower() in SKIP_CATALOGS:
            continue
        try:
            for sch in wc.schemas.list(catalog_name=cat.name):
                if sch.name and sch.name.lower() not in SKIP_SCHEMAS:
                    targets.append((cat.name, sch.name))
        except Exception as exc:
            logger.warning("[schema_scan] could not list schemas in %s: %s", cat.name, exc)

    result: Dict[str, List[Dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for part in pool.map(lambda t: _scan_schema(wc, *t), targets):
            result.update(part)
    logger.info("[schema_scan] schemas=%d tables=%d", len(targets), sum(1 for k in result if not k.endswith("(empty_schema)")))
    return result
