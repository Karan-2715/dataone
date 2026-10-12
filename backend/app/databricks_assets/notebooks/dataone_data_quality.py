# Databricks notebook source
# MAGIC %md
# MAGIC # DataOne — Data Quality
# MAGIC
# MAGIC Profiles Unity Catalog tables with PySpark and reports the scores to the DataOne backend
# MAGIC (`POST /api/v1/data-quality/ingest`), which renders them on the DataOne dashboard.
# MAGIC
# MAGIC | Dimension | How it is measured (0-100) |
# MAGIC |---|---|
# MAGIC | **Completeness** | % of cells that are present. Nulls, NaN and blank strings count as missing. |
# MAGIC | **Uniqueness** | 100 - % of rows that are exact duplicates of another row. |
# MAGIC | **Validity** | % of non-null values passing rules inferred from column names/types (email, phone, non-negative amounts, no future dates). `null` when no column has an applicable rule. |
# MAGIC | **Freshness** | Age of the table's last Delta commit vs. `freshness_sla_hours`: 100 within the SLA, falling linearly to 0 at 7x the SLA. `null` for non-Delta objects. |
# MAGIC
# MAGIC A dimension that cannot be measured is sent as `null` — never a made-up number.
# MAGIC
# MAGIC **Parameters:** `catalog`, `schemas` (comma list, blank = all non-system schemas), `tables` (comma list of
# MAGIC `schema.table`, blank = all), `backend_url`, `secret_scope`/`secret_key` (DataOne ingest key),
# MAGIC `freshness_sla_hours`, `sample_fraction`, `dry_run` (return the payload instead of sending it).
# MAGIC
# MAGIC **Authentication.** If DataOne is hosted on your own server, the ingest key (`dq-ingest-key`) is the only
# MAGIC credential. If DataOne runs as a Databricks App, its proxy also requires an OAuth token: store an OAuth
# MAGIC service principal that has `CAN_USE` on the app as `dq-client-id` / `dq-client-secret` in the same scope.

# COMMAND ----------

import datetime as dt
import logging
import re
import time
import uuid

import requests
from pyspark.sql import DataFrame, functions as F, types as T

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [dq] %(message)s")
logger = logging.getLogger("dataone_dq")

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schemas", "", "Schemas (comma separated, blank = all)")
dbutils.widgets.text("tables", "", "Tables as schema.table (comma separated, blank = all)")
dbutils.widgets.text("backend_url", "https://dataone-1354394467602787.aws.databricksapps.com", "DataOne backend URL")
dbutils.widgets.text("secret_scope", "dataone", "Secret scope")
dbutils.widgets.text("secret_key", "dq-ingest-key", "Secret key holding the DataOne ingest key")
dbutils.widgets.text("freshness_sla_hours", "24", "Freshness SLA (hours)")
dbutils.widgets.text("sample_fraction", "1.0", "Row sample fraction for profiling (0-1]")
dbutils.widgets.dropdown("dry_run", "false", ["false", "true"], "Dry run (do not send)")

CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMAS = [s.strip() for s in dbutils.widgets.get("schemas").split(",") if s.strip()]
TABLES = [t.strip() for t in dbutils.widgets.get("tables").split(",") if t.strip()]
BACKEND_URL = dbutils.widgets.get("backend_url").strip().rstrip("/")
SECRET_SCOPE = dbutils.widgets.get("secret_scope").strip()
SECRET_KEY = dbutils.widgets.get("secret_key").strip()
FRESHNESS_SLA_HOURS = float(dbutils.widgets.get("freshness_sla_hours"))
SAMPLE_FRACTION = float(dbutils.widgets.get("sample_fraction"))
DRY_RUN = dbutils.widgets.get("dry_run") == "true"

MAX_TABLES_PER_RUN = 500        # ingest API limit
ISSUE_COMPLETENESS_BELOW = 95.0  # report columns less complete than this
ISSUE_VALIDITY_BELOW = 99.0
MAX_ISSUES_PER_TABLE = 20        # ingest API limit
IDENT = re.compile(r"^[A-Za-z0-9_\-]+$")

assert CATALOG and IDENT.match(CATALOG), f"invalid catalog: {CATALOG!r}"
assert 0 < SAMPLE_FRACTION <= 1, "sample_fraction must be in (0, 1]"
assert FRESHNESS_SLA_HOURS > 0, "freshness_sla_hours must be > 0"
for part in SCHEMAS + [x for t in TABLES for x in t.split(".")]:
    assert IDENT.match(part), f"invalid identifier: {part!r}"


def q(identifier: str) -> str:
    """Backtick-quote a UC identifier (names are validated against IDENT above)."""
    return "`" + identifier.replace("`", "``") + "`"

# COMMAND ----------
# MAGIC %md ## Table discovery

# COMMAND ----------

SYSTEM_SCHEMAS = {"information_schema"}


def discover_tables() -> list:
    """Return [(schema, table)] for the requested scope."""
    if TABLES:
        found = []
        for t in TABLES:
            parts = t.split(".")
            assert len(parts) == 2, f"tables entries must be schema.table, got {t!r}"
            found.append((parts[0], parts[1]))
        return found

    schemas = SCHEMAS or [
        r["databaseName"] for r in spark.sql(f"SHOW SCHEMAS IN {q(CATALOG)}").collect()
        if r["databaseName"] not in SYSTEM_SCHEMAS
    ]
    found = []
    for schema in schemas:
        for r in spark.sql(f"SHOW TABLES IN {q(CATALOG)}.{q(schema)}").collect():
            if not r["isTemporary"]:
                found.append((schema, r["tableName"]))
    return found

# COMMAND ----------
# MAGIC %md ## Metrics

# COMMAND ----------

EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
PHONE_RE = r"^\+?[0-9][0-9\s\-().]{6,19}$"
NON_NEGATIVE_NAME = re.compile(r"(^|_)(amount|price|cost|qty|quantity|salary|age|count|total|revenue)(_|$)", re.I)
EMAIL_NAME = re.compile(r"e[-_]?mail", re.I)
PHONE_NAME = re.compile(r"(phone|mobile|fax)", re.I)
PAST_DATE_NAME = re.compile(r"(created|updated|modified|ingested|loaded|order|birth|hire|signup|registered)", re.I)

NUMERIC_TYPES = (T.ByteType, T.ShortType, T.IntegerType, T.LongType, T.FloatType, T.DoubleType, T.DecimalType)


def pct(part, whole):
    return None if not whole else round(100.0 * part / whole, 1)


def present_expr(field: T.StructField):
    """1 when the cell holds a real value, else 0 (null / NaN / blank string = missing)."""
    c = F.col(q(field.name))
    missing = c.isNull()
    if isinstance(field.dataType, (T.FloatType, T.DoubleType)):
        missing = missing | F.isnan(c)
    elif isinstance(field.dataType, T.StringType):
        missing = missing | (F.trim(c) == "")
    return F.when(missing, 0).otherwise(1)


def validity_rules(schema: T.StructType) -> list:
    """[(column, rule_name, boolean Column)] — rules inferred from column name + type."""
    rules = []
    now = F.current_timestamp()
    for f in schema.fields:
        c = F.col(q(f.name))
        if isinstance(f.dataType, T.StringType):
            if EMAIL_NAME.search(f.name):
                rules.append((f.name, "valid_email", c.rlike(EMAIL_RE)))
            elif PHONE_NAME.search(f.name):
                rules.append((f.name, "valid_phone", c.rlike(PHONE_RE)))
        elif isinstance(f.dataType, NUMERIC_TYPES) and NON_NEGATIVE_NAME.search(f.name):
            rules.append((f.name, "non_negative", c >= 0))
        elif isinstance(f.dataType, (T.DateType, T.TimestampType)) and PAST_DATE_NAME.search(f.name):
            rules.append((f.name, "not_in_future", c.cast("timestamp") <= now + F.expr("INTERVAL 1 DAY")))
    return rules


def freshness_score(last_modified):
    if last_modified is None:
        return None
    age_h = (dt.datetime.now(dt.timezone.utc) - last_modified).total_seconds() / 3600
    if age_h <= FRESHNESS_SLA_HOURS:
        return 100.0
    full_decay = FRESHNESS_SLA_HOURS * 7
    if age_h >= full_decay:
        return 0.0
    return round(100.0 * (full_decay - age_h) / (full_decay - FRESHNESS_SLA_HOURS), 1)


def last_modified_of(full_name: str):
    """Last Delta commit time; None for views / non-Delta tables."""
    try:
        row = spark.sql(f"DESCRIBE DETAIL {full_name}").first()
        ts = row["lastModified"] if row is not None else None
        if ts is None:
            return None
        return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)
    except Exception as exc:  # views, foreign tables, missing permission
        logger.info("freshness unavailable for %s: %s", full_name, str(exc)[:120])
        return None


def profile_table(schema_name: str, table_name: str) -> dict:
    full_name = f"{q(CATALOG)}.{q(schema_name)}.{q(table_name)}"
    logger.info("profiling %s", full_name)
    df: DataFrame = spark.table(full_name)
    if SAMPLE_FRACTION < 1:
        df = df.sample(fraction=SAMPLE_FRACTION, seed=42)
    fields = df.schema.fields
    rules = validity_rules(df.schema)

    # One scan for row count, per-column presence and validity rule counters.
    aggs = [F.count(F.lit(1)).alias("__rows")]
    for i, f in enumerate(fields):
        aggs.append(F.sum(present_expr(f)).alias(f"p{i}"))
    for i, (col_name, _, cond) in enumerate(rules):
        nn = F.col(q(col_name)).isNotNull()
        aggs.append(F.sum(F.when(nn, 1).otherwise(0)).alias(f"vn{i}"))
        aggs.append(F.sum(F.when(nn & cond, 1).otherwise(0)).alias(f"vp{i}"))
    stats = df.agg(*aggs).first()

    rows = stats["__rows"] or 0
    result = {
        "table_name": f"{CATALOG}.{schema_name}.{table_name}",
        "row_count": rows, "column_count": len(fields),
        "completeness": None, "uniqueness": None, "validity": None, "freshness": None,
        "last_modified": None, "issues": [],
    }
    issues = []

    if rows and fields:
        present = [stats[f"p{i}"] or 0 for i in range(len(fields))]
        result["completeness"] = pct(sum(present), rows * len(fields))
        for f, p in zip(fields, present):
            col_pct = pct(p, rows)
            if col_pct is not None and col_pct < ISSUE_COMPLETENESS_BELOW:
                issues.append((col_pct, {"column": f.name, "check": "completeness",
                                         "detail": f"{col_pct}% of values present ({rows - p} missing)"}))

    # Exact-duplicate rows. Map columns cannot be compared, so they are excluded.
    comparable = [f.name for f in fields if not isinstance(f.dataType, T.MapType)]
    if rows and comparable:
        distinct_rows = df.select(*[q(c) for c in comparable]).distinct().count()
        dupes = rows - distinct_rows
        result["uniqueness"] = pct(distinct_rows, rows)
        if dupes:
            issues.append((result["uniqueness"], {"column": None, "check": "uniqueness",
                                                  "detail": f"{dupes} duplicate rows ({100 - result['uniqueness']:.1f}%)"}))

    checked = passed = 0
    for i, (col_name, rule_name, _) in enumerate(rules):
        vn, vp = stats[f"vn{i}"] or 0, stats[f"vp{i}"] or 0
        checked += vn
        passed += vp
        col_pct = pct(vp, vn)
        if col_pct is not None and col_pct < ISSUE_VALIDITY_BELOW:
            issues.append((col_pct, {"column": col_name, "check": rule_name,
                                     "detail": f"{col_pct}% valid ({vn - vp} of {vn} fail {rule_name})"}))
    result["validity"] = pct(passed, checked)

    last_modified = last_modified_of(full_name)
    if last_modified is not None:
        result["last_modified"] = last_modified.isoformat()
        result["freshness"] = freshness_score(last_modified)
        if result["freshness"] is not None and result["freshness"] < 100:
            age_h = (dt.datetime.now(dt.timezone.utc) - last_modified).total_seconds() / 3600
            issues.append((result["freshness"], {"column": None, "check": "freshness",
                                                 "detail": f"last updated {age_h:.0f}h ago (SLA {FRESHNESS_SLA_HOURS:.0f}h)"}))

    result["issues"] = [i for _, i in sorted(issues, key=lambda x: x[0])][:MAX_ISSUES_PER_TABLE]
    return result

# COMMAND ----------
# MAGIC %md ## Run

# COMMAND ----------

targets = discover_tables()
if not targets:
    raise RuntimeError(f"No tables found in {CATALOG} (schemas={SCHEMAS or 'all'}, tables={TABLES or 'all'})")
if len(targets) > MAX_TABLES_PER_RUN:
    raise RuntimeError(f"{len(targets)} tables exceeds the {MAX_TABLES_PER_RUN}-table limit per run; narrow `schemas`/`tables`.")

results = []
for schema_name, table_name in targets:
    try:
        results.append(profile_table(schema_name, table_name))
    except Exception as exc:
        # Report the failure instead of silently dropping the table or inventing a score.
        logger.error("profiling failed for %s.%s: %s", schema_name, table_name, exc)
        results.append({
            "table_name": f"{CATALOG}.{schema_name}.{table_name}", "row_count": None, "column_count": None,
            "completeness": None, "uniqueness": None, "validity": None, "freshness": None,
            "issues": [{"column": None, "check": "profile_error", "detail": str(exc)[:480]}],
        })

scored = [r for r in results if any(r[d] is not None for d in ("completeness", "uniqueness", "validity", "freshness"))]
logger.info("profiled %d tables (%d scored, %d failed)", len(results), len(scored), len(results) - len(scored))
display(spark.createDataFrame([
    (r["table_name"], r["row_count"], r["completeness"], r["uniqueness"], r["validity"], r["freshness"])
    for r in results
], "table string, rows long, completeness double, uniqueness double, validity double, freshness double"))

# COMMAND ----------
# MAGIC %md ## Send to DataOne

# COMMAND ----------

def post_with_retry(url: str, payload: dict, headers: dict, attempts: int = 4) -> dict:
    """Retry connection errors / 5xx with exponential backoff; 4xx is a caller bug and fails fast."""
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            if resp.status_code < 500:
                if not resp.ok:
                    raise RuntimeError(f"DataOne rejected the report: HTTP {resp.status_code} {resp.text[:500]}")
                return resp.json()
            logger.warning("backend returned %s (attempt %d/%d)", resp.status_code, attempt, attempts)
        except requests.RequestException as exc:
            logger.warning("request failed (attempt %d/%d): %s", attempt, attempts, exc)
        if attempt < attempts:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not reach DataOne at {url} after {attempts} attempts")


def behind_apps_proxy() -> bool:
    return BACKEND_URL.split("//", 1)[-1].split("/", 1)[0].endswith(".databricksapps.com")


def proxy_token() -> str:
    """OAuth token accepted by the Databricks Apps proxy (service principal, client-credentials flow)."""
    try:
        client_id = dbutils.secrets.get(SECRET_SCOPE, "dq-client-id")
        client_secret = dbutils.secrets.get(SECRET_SCOPE, "dq-client-secret")
    except Exception as exc:
        raise RuntimeError(
            f"Secrets dq-client-id / dq-client-secret not found in scope '{SECRET_SCOPE}'. The Databricks Apps proxy "
            "only accepts OAuth tokens: create an OAuth secret for a service principal with CAN_USE on the DataOne "
            "app and store it there."
        ) from exc
    host = spark.conf.get("spark.databricks.workspaceUrl")
    resp = requests.post(
        f"https://{host}/oidc/v1/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials", "scope": "all-apis"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


run_id = str(uuid.uuid4())
payload = {
    "run_id": run_id,
    "source": "databricks",
    "catalog": CATALOG,
    "schema_name": SCHEMAS[0] if len(SCHEMAS) == 1 else None,
    "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "tables": results,
}

if DRY_RUN:
    import json
    logger.info("dry run: not sending %d tables to DataOne", len(results))
    dbutils.notebook.exit(json.dumps(payload))

# The DataOne ingest key always travels in its own header. `Authorization` is only needed (and only
# accepted) when DataOne is a Databricks App, where the proxy checks it before the request reaches DataOne.
headers = {"X-DataOne-Ingest-Key": dbutils.secrets.get(SECRET_SCOPE, SECRET_KEY)}
if behind_apps_proxy():
    headers["Authorization"] = f"Bearer {proxy_token()}"
reply = post_with_retry(f"{BACKEND_URL}/api/v1/data-quality/ingest", payload, headers)
logger.info("DataOne accepted run %s: %s", run_id, reply)
print(reply)
