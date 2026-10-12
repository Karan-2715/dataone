"""
Databricks Ingestion Service

Handles:
  1. Creating Databricks Jobs idempotently per source type (stored in IngestionPipelineCatalog)
  2. Triggering job runs with connection parameters as job params
  3. Polling run status from Databricks and syncing to DataOne DB
  4. Genie AI pipeline triggering

Design: DataOne owns the pipeline code. Each source type has an embedded
PySpark/DLT task definition that is submitted to Databricks Jobs API on
first use for that source type. Subsequent triggers reuse the same job.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ingestion_run import IngestionRun, IngestionPipelineCatalog
from app.models.connection import DBConnection

logger = logging.getLogger(__name__)


# ── Embedded DLT / PySpark task Python code per source type ─────────────────
# These are submitted as notebook-less Python tasks to Databricks Jobs API.
# Each script reads from source using params passed via spark.conf / sys.argv,
# applies DQ rules, and writes to target Delta Lake.

INGESTION_SCRIPTS: Dict[str, str] = {
    "mysql": '''
# Databricks notebook source
# COMMAND ----------
%pip install pymysql pandas
dbutils.library.restartPython()

# COMMAND ----------
import sys
import pymysql
import pandas as pd
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("DataOne_MySQL_Ingestion").getOrCreate()

source_host = dbutils.widgets.get("dataone.source.host")
source_port = dbutils.widgets.get("dataone.source.port")
if not source_port: source_port = "3306"
source_db = dbutils.widgets.get("dataone.source.database")
source_user = dbutils.widgets.get("dataone.source.username")
source_password = dbutils.widgets.get("dataone.source.password")
target_catalog = dbutils.widgets.get("dataone.target.catalog")
if not target_catalog: target_catalog = "workspace"
target_schema = dbutils.widgets.get("dataone.target.schema")
if not target_schema: target_schema = "default"

print(f"Connecting to MySQL at {source_host}:{source_port}...")
conn = pymysql.connect(host=source_host, port=int(source_port), user=source_user, password=source_password, database=source_db)

try:
    tables_df = pd.read_sql(f"SELECT table_name FROM information_schema.tables WHERE table_schema='{source_db}'", conn)
    tables = tables_df.iloc[:, 0].tolist()

    for table in tables:
        print(f"Extracting {table}...")
        df_pd = pd.read_sql(f"SELECT * FROM {table}", conn)
        
        if df_pd.empty:
            print(f"Table {table} is empty. Skipping.")
            continue
            
        df = spark.createDataFrame(df_pd)
        df = df.dropDuplicates()
        
        target_table = f"{target_catalog}.{target_schema}.{source_db}_{table}"
        
        # Ensure schema exists
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {target_catalog}.{target_schema}")
        
        df.write.format("delta").mode("overwrite").option("mergeSchema", "true").saveAsTable(target_table)
        print(f"[DataOne] Ingested {df.count()} rows into {target_table}")
finally:
    conn.close()

print("[DataOne] MySQL ingestion complete")
''',

    "postgres": '''
# Databricks notebook source
# COMMAND ----------
%pip install psycopg2-binary pandas
dbutils.library.restartPython()

# COMMAND ----------
import sys
import psycopg2
import pandas as pd
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("DataOne_PostgreSQL_Ingestion").getOrCreate()

source_host = dbutils.widgets.get("dataone.source.host")
source_port = dbutils.widgets.get("dataone.source.port")
if not source_port: source_port = "5432"
source_db = dbutils.widgets.get("dataone.source.database")
source_user = dbutils.widgets.get("dataone.source.username")
source_password = dbutils.widgets.get("dataone.source.password")
target_catalog = dbutils.widgets.get("dataone.target.catalog")
if not target_catalog: target_catalog = "workspace"
target_schema = dbutils.widgets.get("dataone.target.schema")
if not target_schema: target_schema = "default"

print(f"Connecting to Postgres at {source_host}:{source_port}...")
conn = psycopg2.connect(host=source_host, port=int(source_port), user=source_user, password=source_password, dbname=source_db)

try:
    tables_df = pd.read_sql("SELECT table_name FROM information_schema.tables WHERE table_schema='public'", conn)
    tables = tables_df.iloc[:, 0].tolist()

    for table in tables:
        print(f"Extracting {table}...")
        df_pd = pd.read_sql(f"SELECT * FROM public.{table}", conn)
        
        if df_pd.empty:
            print(f"Table {table} is empty. Skipping.")
            continue
            
        df = spark.createDataFrame(df_pd)
        df = df.dropDuplicates()
        
        target_table = f"{target_catalog}.{target_schema}.{source_db}_{table}"
        
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {target_catalog}.{target_schema}")
        df.write.format("delta").mode("overwrite").option("mergeSchema", "true").saveAsTable(target_table)
        print(f"[DataOne] Ingested {df.count()} rows into {target_table}")
finally:
    conn.close()

print("[DataOne] PostgreSQL ingestion complete")
''',

    "mongodb": '''
# Databricks notebook source
# COMMAND ----------
%pip install pymongo pandas
dbutils.library.restartPython()

# COMMAND ----------
import sys
import pymongo
import pandas as pd
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("DataOne_MongoDB_Ingestion").getOrCreate()

source_host = dbutils.widgets.get("dataone.source.host")
source_port = dbutils.widgets.get("dataone.source.port")
if not source_port: source_port = "27017"
source_db = dbutils.widgets.get("dataone.source.database")
source_user = dbutils.widgets.get("dataone.source.username")
source_password = dbutils.widgets.get("dataone.source.password")
target_catalog = dbutils.widgets.get("dataone.target.catalog")
if not target_catalog: target_catalog = "workspace"
target_schema = dbutils.widgets.get("dataone.target.schema")
if not target_schema: target_schema = "default"

print(f"Connecting to MongoDB at {source_host}:{source_port}...")
mongo_uri = f"mongodb://{source_user}:{source_password}@{source_host}:{source_port}/?authSource={source_db}"
client = pymongo.MongoClient(mongo_uri)
db = client[source_db]

try:
    collections = db.list_collection_names()
    
    for coll in collections:
        print(f"Extracting {coll}...")
        cursor = db[coll].find()
        
        # Convert to pandas
        records = list(cursor)
        if not records:
            print(f"Collection {coll} is empty. Skipping.")
            continue
            
        # Convert ObjectIds to strings
        for doc in records:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
                
        df_pd = pd.DataFrame(records)
        # Convert any nested dicts/lists to strings to prevent PySpark schema issues
        for col in df_pd.columns:
            if df_pd[col].dtype == 'object':
                df_pd[col] = df_pd[col].astype(str)
                
        df = spark.createDataFrame(df_pd)
        df = df.dropDuplicates()
        
        target_table = f"{target_catalog}.{target_schema}.{source_db}_{coll}"
        
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {target_catalog}.{target_schema}")
        df.write.format("delta").mode("overwrite").option("mergeSchema", "true").saveAsTable(target_table)
        print(f"[DataOne] Ingested {df.count()} rows into {target_table}")
finally:
    client.close()

print("[DataOne] MongoDB ingestion complete")
''',

    "csv": '''
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("DataOne_CSV_Ingestion").getOrCreate()

csv_path = spark.conf.get("dataone.source.path")  # dbfs:// or s3:// or /Volumes/...
target_catalog = spark.conf.get("dataone.target.catalog", "workspace")
target_schema = spark.conf.get("dataone.target.schema", "default")
target_table_name = spark.conf.get("dataone.target.table", "csv_import")

df = spark.read.option("header", "true").option("inferSchema", "true").csv(csv_path)
df = df.dropDuplicates()
# Drop rows where ALL values are null
df = df.dropna(how="all")

target_table = f"{target_catalog}.{target_schema}.{target_table_name}"
df.write.format("delta").mode("overwrite").option("mergeSchema", "true").saveAsTable(target_table)
print(f"[DataOne] Ingested {df.count()} rows from CSV -> {target_table}")
''',

    "snowflake": '''
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("DataOne_Snowflake_Ingestion").getOrCreate()

sfUrl = spark.conf.get("dataone.source.host")
sfUser = spark.conf.get("dataone.source.username")
sfPassword = spark.conf.get("dataone.source.password")
sfDatabase = spark.conf.get("dataone.source.database")
sfSchema = spark.conf.get("dataone.source.schema", "PUBLIC")
sfWarehouse = spark.conf.get("dataone.source.warehouse", "COMPUTE_WH")
target_catalog = spark.conf.get("dataone.target.catalog", "workspace")
target_schema_name = spark.conf.get("dataone.target.schema", "default")

snowflake_opts = {
    "sfUrl": sfUrl, "sfUser": sfUser, "sfPassword": sfPassword,
    "sfDatabase": sfDatabase, "sfSchema": sfSchema, "sfWarehouse": sfWarehouse,
}

tables_df = (spark.read.format("net.snowflake.spark.snowflake")
    .options(**snowflake_opts)
    .option("query", f"SELECT table_name FROM information_schema.tables WHERE table_schema='{sfSchema}'")
    .load())

for row in tables_df.collect():
    table = row.TABLE_NAME
    df = (spark.read.format("net.snowflake.spark.snowflake")
          .options(**snowflake_opts).option("dbtable", table).load())
    df = df.dropDuplicates()
    target_table = f"{target_catalog}.{target_schema_name}.{sfDatabase}_{table}"
    df.write.format("delta").mode("overwrite").option("mergeSchema", "true").saveAsTable(target_table)
    print(f"[DataOne] Ingested {table} -> {target_table}")

print("[DataOne] Snowflake ingestion complete")
''',

    "sqlserver": '''
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("DataOne_SQLServer_Ingestion").getOrCreate()

source_catalog = "sqlserver_catalog"
try:
    source_catalog = spark.conf.get("dataone.source.catalog") or source_catalog
except:
    pass

source_db = "default_db"
try:
    source_db = spark.conf.get("dataone.source.database") or source_db
except:
    pass
target_catalog = spark.conf.get("dataone.target.catalog", "workspace")
target_schema = spark.conf.get("dataone.target.schema", "default")

tables_df = spark.sql(f"SELECT table_name FROM {source_catalog}.information_schema.tables WHERE table_catalog='{source_db}' AND table_type='BASE TABLE'")

for row in tables_df.collect():
    table = row.table_name
    df = spark.table(f"{source_catalog}.dbo.{table}")
    df = df.dropDuplicates()
    target_table = f"{target_catalog}.{target_schema}.{source_db}_{table}"
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {target_catalog}.{target_schema}")
    df.write.format("delta").mode("overwrite").option("mergeSchema", "true").saveAsTable(target_table)
    print(f"[DataOne] Ingested {table} -> {target_table}")

print("[DataOne] SQL Server ingestion complete")
''',
}


SOURCE_TYPE_DISPLAY = {
    "mysql": "MySQL", "postgres": "PostgreSQL", "mongodb": "MongoDB",
    "csv": "CSV/S3", "snowflake": "Snowflake", "sqlserver": "SQL Server",
    "redshift": "Amazon Redshift", "oracle": "Oracle", "salesforce": "Salesforce",
}


def _get_workspace_client(token: Optional[str] = None):
    """Return a Databricks WorkspaceClient using provided token or env config.
    
    Priority order:
    1. User token passed as parameter (from user.databricks_access_token)
    2. Environment DATABRICKS_ACCESS_TOKEN
    3. OAuth M2M credentials from environment
    """
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.config import Config
        import os
        
        # Native Databricks Apps mode uses DATABRICKS_HOST
        host = settings.DATABRICKS_WORKSPACE_URL
        if not host and settings.DATABRICKS_HOST:
            host = f"https://{settings.DATABRICKS_HOST}"

        if token:
            # When user token is provided, use ONLY that token
            # Temporarily unset OAuth env vars to prevent SDK auto-detection
            saved_client_id = os.environ.pop('DATABRICKS_CLIENT_ID', None)
            saved_client_secret = os.environ.pop('DATABRICKS_CLIENT_SECRET', None)
            
            try:
                config = Config(
                    host=host,
                    token=token,
                )
                client = WorkspaceClient(config=config)
            finally:
                # Restore env vars
                if saved_client_id:
                    os.environ['DATABRICKS_CLIENT_ID'] = saved_client_id
                if saved_client_secret:
                    os.environ['DATABRICKS_CLIENT_SECRET'] = saved_client_secret
            
            return client
            
        elif settings.DATABRICKS_ACCESS_TOKEN:
            # Env token provided - also need to disable OAuth
            saved_client_id = os.environ.pop('DATABRICKS_CLIENT_ID', None)
            saved_client_secret = os.environ.pop('DATABRICKS_CLIENT_SECRET', None)
            
            try:
                config = Config(
                    host=host,
                    token=settings.DATABRICKS_ACCESS_TOKEN,
                )
                client = WorkspaceClient(config=config)
            finally:
                if saved_client_id:
                    os.environ['DATABRICKS_CLIENT_ID'] = saved_client_id
                if saved_client_secret:
                    os.environ['DATABRICKS_CLIENT_SECRET'] = saved_client_secret
            
            return client
        else:
            # Fall back to M2M OAuth (via DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET from env)
            return WorkspaceClient(host=host)
    except Exception as e:
        logger.error("[databricks_ingestion] stage=get_client failed: %s", e)
        raise


def _build_job_params(source_conn: DBConnection, target_conn: Optional[DBConnection]) -> Dict[str, str]:
    """Build Databricks spark_conf params from DataOne connection objects."""
    params = {}
    cfg = source_conn.config or {}
    src_type = source_conn.type.lower()

    if src_type in ("mysql", "postgres", "sqlserver", "oracle"):
        params["dataone.source.catalog"] = cfg.get("catalog", f"{src_type}_catalog")
        params["dataone.source.host"] = cfg.get("host", "")
        params["dataone.source.port"] = str(cfg.get("port", ""))
        params["dataone.source.database"] = cfg.get("database", cfg.get("dbname", ""))
        params["dataone.source.username"] = cfg.get("username", cfg.get("user", ""))
        params["dataone.source.password"] = cfg.get("password", "")
    elif src_type == "mongodb":
        params["dataone.source.connection_string"] = cfg.get("connection_string",
            f"mongodb://{cfg.get('username','')}:{cfg.get('password','')}@{cfg.get('host','localhost')}:{cfg.get('port', 27017)}")
        params["dataone.source.database"] = cfg.get("database", "")
        params["dataone.source.collections"] = cfg.get("collections", "")
    elif src_type == "snowflake":
        params["dataone.source.host"] = cfg.get("account", cfg.get("host", "")) + ".snowflakecomputing.com"
        params["dataone.source.username"] = cfg.get("username", "")
        params["dataone.source.password"] = cfg.get("password", "")
        params["dataone.source.database"] = cfg.get("database", "")
        params["dataone.source.schema"] = cfg.get("schema", "PUBLIC")
        params["dataone.source.warehouse"] = cfg.get("warehouse", "COMPUTE_WH")
    elif src_type == "csv":
        params["dataone.source.path"] = cfg.get("path", cfg.get("url", ""))
        params["dataone.target.table"] = cfg.get("table_name", "csv_import")

    # Target params
    if target_conn:
        tcfg = target_conn.config or {}
        params["dataone.target.catalog"] = tcfg.get("catalog", "workspace")
        params["dataone.target.schema"] = tcfg.get("schema", "default")

    return params


class DatabricksIngestionService:
    """Service for creating and triggering Databricks ingestion pipelines from DataOne."""


    @staticmethod
    def setup_lakehouse_federation(source_conn, actor: str, user_token=None):
        logger.info("[databricks_ingestion] stage=setup_lakehouse_federation source_connection_id=%s actor=%s", source_conn.id, actor)
        
        src_type = source_conn.type.lower()
        if src_type not in ("mysql", "postgres", "sqlserver", "snowflake"):
            logger.info("Connection type %s not supported for Lakehouse Federation", src_type)
            return {"status": "unsupported", "type": src_type}
        
        cfg = source_conn.config or {}
        host = cfg.get("host", cfg.get("account", ""))
        port = cfg.get("port", "")
        if src_type == "snowflake":
            port = "443"
        elif not port:
            port = "3306" if src_type == "mysql" else "5432" if src_type == "postgres" else "1433"
            
        user = cfg.get("username", cfg.get("user", ""))
        password = cfg.get("password", "")
        
        import re as regex
        clean_host = regex.sub(r'[^A-Za-z0-9_]', '_', host.split('.')[0])
        connection_name = f"dataone_{src_type}_{clean_host}_conn_{source_conn.id}".lower()
        catalog_name = cfg.get("catalog") or f"dataone_{src_type}_{clean_host}_catalog".lower()
        
        try:
            ws = _get_workspace_client(user_token)
            from databricks.sdk.service import catalog
            
            # Map type
            type_map = {
                "mysql": catalog.ConnectionType.MYSQL,
                "postgres": catalog.ConnectionType.POSTGRESQL,
                "sqlserver": catalog.ConnectionType.SQLSERVER,
                "snowflake": catalog.ConnectionType.SNOWFLAKE
            }
            conn_type = type_map[src_type]
            
            options = {
                "host": host,
                "port": str(port),
                "user": user,
                "password": password
            }
            
            if src_type == "snowflake":
                options["warehouse"] = cfg.get("warehouse", "")
                options["database"] = cfg.get("database", "")
                
            logger.info("Creating Databricks connection: %s", connection_name)
            try:
                ws.connections.create(
                    name=connection_name,
                    connection_type=conn_type,
                    options=options,
                    comment=f"Created by DataOne for source connection {source_conn.id}"
                )
            except Exception as e:
                if "already exists" in str(e).lower() or "already_exists" in str(e).lower():
                    logger.warning("Connection already exists: %s", e)
                else:
                    logger.error("Failed to create connection: %s", e)
                    raise
                
            logger.info("Creating Databricks foreign catalog: %s", catalog_name)
            try:
                catalog_options = {}
                if src_type in ("postgres", "sqlserver"):
                    catalog_options["database"] = cfg.get("database", cfg.get("dbname", ""))
                    
                ws.catalogs.create(
                    name=catalog_name,
                    connection_name=connection_name,
                    options=catalog_options,
                    comment=f"Foreign catalog for DataOne source connection {source_conn.id}"
                )
            except Exception as e:
                if "already exists" in str(e).lower() or "already_exists" in str(e).lower():
                    logger.warning("Catalog already exists: %s", e)
                else:
                    logger.error("Failed to create foreign catalog: %s", e)
                    raise
            
            try:
                from databricks.sdk.service import catalog as cat_svc
                ws.grants.update(
                    securable_type=cat_svc.SecurableType.CATALOG,
                    full_name=catalog_name,
                    changes=[
                        cat_svc.PermissionsChange(
                            principal="account users",
                            add=[cat_svc.Privilege.USE_CATALOG, cat_svc.Privilege.USE_SCHEMA, cat_svc.Privilege.SELECT]
                        )
                    ]
                )
                logger.info("Granted permissions to account users for %s", catalog_name)
            except Exception as e:
                logger.warning("Failed to grant permissions on catalog: %s", e)
                
            return {
                "status": "success",
                "connection_name": connection_name,
                "catalog_name": catalog_name
            }
            
        except Exception as e:
            logger.error("[databricks_ingestion] Federation setup failed: %s", e)
            raise

    @staticmethod
    def get_or_create_pipeline(
        source_type: str,
        db: Session,
        user_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Idempotently get or create a Databricks Job for the given source type.
        Checks IngestionPipelineCatalog first; creates the Databricks Job if missing.
        Returns {job_id, job_name}.
        """
        logger.info("[databricks_ingestion] stage=get_or_create_pipeline source_type=%s", source_type)

        # Check local catalog first
        catalog_entry = (
            db.query(IngestionPipelineCatalog)
            .filter(IngestionPipelineCatalog.source_type == source_type)
            .first()
        )
        if catalog_entry:
            if catalog_entry.pipeline_type == "mock":
                # If it's a mock pipeline but we now have real credentials, delete it so we can create a real one
                import os
                has_creds = bool((settings.DATABRICKS_WORKSPACE_URL and settings.DATABRICKS_ACCESS_TOKEN) or 
                                 (settings.DATABRICKS_HOST and user_token) or 
                                 (settings.DATABRICKS_HOST and os.getenv("DATABRICKS_CLIENT_ID")))
                if has_creds:
                    logger.info(f"Removing mock pipeline {catalog_entry.databricks_job_id} to create a real one.")
                    db.delete(catalog_entry)
                    db.commit()
                else:
                    return {
                        "job_id": catalog_entry.databricks_job_id,
                        "job_name": catalog_entry.job_name,
                        "created": False,
                        "mock": True
                    }
            else:
                logger.info(
                    "[databricks_ingestion] stage=reuse_existing_job job_id=%s",
                    catalog_entry.databricks_job_id,
                )
                
                # Ensure the Databricks notebook is updated with the latest codebase script
                script = INGESTION_SCRIPTS.get(source_type, INGESTION_SCRIPTS.get("mysql", ""))
                script_path = f"/Shared/DataOne/Notebooks/{source_type}_ingestion.py"
                
                try:
                    ws = _get_workspace_client(user_token)
                    from databricks.sdk.service.workspace import ImportFormat, Language
                    import base64
                    script_bytes = script.encode("utf-8")
                    try:
                        ws.workspace.mkdirs("/Shared/DataOne")
                        ws.workspace.mkdirs("/Shared/DataOne/Notebooks")
                    except Exception:
                        pass
                    ws.workspace.import_(
                        path=script_path,
                        format=ImportFormat.SOURCE,
                        language=Language.PYTHON,
                        content=base64.b64encode(script_bytes).decode("utf-8"),
                        overwrite=True,
                    )
                    logger.info("[databricks_ingestion] stage=script_updated_on_reuse path=%s", script_path)
                except Exception as upload_err:
                    logger.warning("[databricks_ingestion] stage=script_update_failed_on_reuse: %s", upload_err)

                catalog_entry.last_used_at = datetime.utcnow()
                db.commit()
                return {
                    "job_id": catalog_entry.databricks_job_id,
                    "job_name": catalog_entry.job_name,
                    "created": False,
                    "mock": False
                }

        # Get script for this source type (fallback to generic JDBC)
        script = INGESTION_SCRIPTS.get(source_type, INGESTION_SCRIPTS.get("mysql", ""))
        job_name = f"DataOne_{SOURCE_TYPE_DISPLAY.get(source_type, source_type.title())}_Ingestion"

        # Check for either explicit URL/PAT, Native App user token, or M2M OAuth
        has_explicit_creds = bool(settings.DATABRICKS_WORKSPACE_URL and settings.DATABRICKS_ACCESS_TOKEN)
        has_native_creds = bool(settings.DATABRICKS_HOST and user_token)
        import os
        has_m2m_creds = bool(settings.DATABRICKS_HOST and os.getenv("DATABRICKS_CLIENT_ID") and os.getenv("DATABRICKS_CLIENT_SECRET"))
        
        if not has_explicit_creds and not has_native_creds and not has_m2m_creds:
            logger.warning(
                "[databricks_ingestion] stage=no_credentials — Databricks not configured, returning mock job_id"
            )
            # In dev/test mode without Databricks, store a mock entry
            mock_entry = IngestionPipelineCatalog(
                source_type=source_type,
                databricks_job_id=999000 + len(source_type),
                job_name=job_name,
                pipeline_type="mock",
            )
            db.add(mock_entry)
            db.commit()
            return {"job_id": mock_entry.databricks_job_id, "job_name": job_name, "created": True, "mock": True}

        try:
            ws = _get_workspace_client(user_token)

            # Create Databricks Job with embedded Python Notebook
            from databricks.sdk.service.jobs import (
                JobSettings, Task, NotebookTask
            )
            from databricks.sdk.service.workspace import ImportFormat
            from databricks.sdk.service.workspace import Language
            
            script_path = f"/Shared/DataOne/Notebooks/{source_type}_ingestion.py"

            job_settings = JobSettings(
                name=job_name,
                max_concurrent_runs=3,
                tasks=[
                    Task(
                        task_key="ingestion",
                        notebook_task=NotebookTask(
                            notebook_path=script_path,
                        ),
                        timeout_seconds=7200,
                    )
                ],
            )

            # Upload the script to Workspace Files instead of DBFS
            try:
                import base64
                script_bytes = script.encode("utf-8")
                try:
                    ws.workspace.mkdirs("/Shared/DataOne")
                    ws.workspace.mkdirs("/Shared/DataOne/Notebooks")
                except Exception:
                    pass
                ws.workspace.import_(
                    path=script_path,
                    format=ImportFormat.SOURCE,
                    language=Language.PYTHON,
                    content=base64.b64encode(script_bytes).decode("utf-8"),
                    overwrite=True,
                )
                logger.info("[databricks_ingestion] stage=script_uploaded path=%s", script_path)
            except Exception as upload_err:
                logger.warning("[databricks_ingestion] stage=script_upload_failed: %s", upload_err)

            created_job = ws.jobs.create(
                name=job_settings.name,
                tasks=job_settings.tasks,
                environments=job_settings.environments
            )
            job_id = created_job.job_id

            logger.info("[databricks_ingestion] stage=job_created job_id=%s source_type=%s", job_id, source_type)

            # Persist to catalog
            catalog_entry = IngestionPipelineCatalog(
                source_type=source_type,
                databricks_job_id=job_id,
                job_name=job_name,
                pipeline_type="job",
                last_used_at=datetime.utcnow(),
            )
            db.add(catalog_entry)
            db.commit()

            return {"job_id": job_id, "job_name": job_name, "created": True}

        except Exception as e:
            logger.error("[databricks_ingestion] stage=create_pipeline_failed source_type=%s: %s", source_type, e)
            raise

    @staticmethod
    def trigger_ingestion(
        source_connection_id: int,
        target_connection_id: Optional[int],
        target_catalog: str,
        target_schema: str,
        db: Session,
        actor: str,
        user_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Trigger a Databricks ingestion job for the given source/target connections.
        Returns IngestionRun record dict with databricks_run_id.
        """
        logger.info(
            "[databricks_ingestion] stage=trigger source=%s target=%s actor=%s",
            source_connection_id, target_connection_id, actor,
        )

        # Fetch connections
        source_conn = db.query(DBConnection).filter(
            DBConnection.id == source_connection_id, DBConnection.is_deleted == False
        ).first()
        if not source_conn:
            raise ValueError(f"Source connection {source_connection_id} not found")

        target_conn = None
        if target_connection_id:
            target_conn = db.query(DBConnection).filter(
                DBConnection.id == target_connection_id, DBConnection.is_deleted == False
            ).first()

        source_type = source_conn.type.lower()

        # Get or create the Databricks job
        pipeline_info = DatabricksIngestionService.get_or_create_pipeline(
            source_type, db, user_token=user_token
        )
        job_id = pipeline_info["job_id"]
        is_mock = pipeline_info.get("mock", False)

        # Build params
        params = _build_job_params(source_conn, target_conn)
        
        # Override target catalog/schema if explicitly provided (even if target_conn is None)
        params["dataone.target.catalog"] = target_catalog or "workspace"
        params["dataone.target.schema"] = target_schema or "default"

        # Create IngestionRun record
        run_record = IngestionRun(
            source_type=source_type,
            source_connection_id=source_connection_id,
            target_connection_id=target_connection_id,
            databricks_job_id=job_id,
            status="pending",
            actor=actor,
            trigger_params=params,
        )
        db.add(run_record)
        db.flush()  # get run_record.id

        if is_mock:
            # Dev mode — simulate a run
            run_record.databricks_run_id = 99900000 + run_record.id
            run_record.status = "running"
            run_record.databricks_run_url = "https://mock.databricks.com/run"
            db.commit()
            logger.info("[databricks_ingestion] stage=mock_run id=%s", run_record.id)
            return _run_to_dict(run_record)

        try:
            ws = _get_workspace_client(user_token)

            # Trigger job run with notebook_params
            run_response = ws.jobs.run_now(
                job_id=job_id,
                notebook_params={k: str(v) for k, v in params.items()},
            )
            databricks_run_id = run_response.run_id

            # Build run URL
            run_url = f"{settings.DATABRICKS_WORKSPACE_URL}#job/{job_id}/run/{databricks_run_id}"

            run_record.databricks_run_id = databricks_run_id
            run_record.status = "running"
            run_record.databricks_run_url = run_url
            db.commit()

            logger.info(
                "[databricks_ingestion] stage=triggered run_id=%s job_id=%s source=%s",
                databricks_run_id, job_id, source_type,
            )

            return _run_to_dict(run_record)

        except Exception as e:
            run_record.status = "failed"
            run_record.error_message = str(e)
            db.commit()
            logger.error("[databricks_ingestion] stage=trigger_failed: %s", e)
            raise

    @staticmethod
    def get_run_status(
        ingestion_run_id: int,
        db: Session,
        user_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get the latest status of an ingestion run.
        Syncs status from Databricks if run_id is available.
        """
        logger.info("[databricks_ingestion] stage=get_status ingestion_run_id=%s", ingestion_run_id)

        run_record = db.query(IngestionRun).filter(IngestionRun.id == ingestion_run_id).first()
        if not run_record:
            raise ValueError(f"IngestionRun {ingestion_run_id} not found")

        # If terminal status, return cached
        if run_record.status in ("succeeded", "failed", "cancelled"):
            return _run_to_dict(run_record)

        # If no Databricks run_id or mock mode, return as-is
        if not run_record.databricks_run_id or run_record.databricks_run_id >= 99900000:
            return _run_to_dict(run_record)

        try:
            ws = _get_workspace_client(user_token)
            run_info = ws.jobs.get_run(run_id=run_record.databricks_run_id)

            state = run_info.state
            life_cycle = state.life_cycle_state.value if state and state.life_cycle_state else "RUNNING"
            result_state = state.result_state.value if state and state.result_state else None

            # Map Databricks states to DataOne states
            if life_cycle in ("PENDING", "WAITING_FOR_RETRY"):
                new_status = "pending"
            elif life_cycle in ("RUNNING", "BLOCKED"):
                new_status = "running"
            elif life_cycle == "TERMINATED":
                new_status = "succeeded" if result_state == "SUCCESS" else "failed"
            elif life_cycle == "SKIPPED":
                new_status = "cancelled"
            else:
                new_status = "running"

            run_record.status = new_status
            if new_status in ("succeeded", "failed", "cancelled"):
                run_record.completed_at = datetime.utcnow()
                if result_state != "SUCCESS" and state:
                    run_record.error_message = state.state_message
                
                # If succeeded, trigger metadata capture from target tables
                if new_status == "succeeded":
                    try:
                        DatabricksIngestionService._capture_ingestion_metadata(
                            run_record, db, user_token
                        )
                    except Exception as meta_err:
                        logger.warning(
                            "[databricks_ingestion] stage=metadata_capture_failed (non-fatal): %s",
                            meta_err
                        )

            db.commit()
            logger.info(
                "[databricks_ingestion] stage=status_synced run_id=%s status=%s",
                run_record.databricks_run_id, new_status,
            )

        except Exception as e:
            logger.warning("[databricks_ingestion] stage=status_sync_failed (non-fatal): %s", e)

        return _run_to_dict(run_record)

    @staticmethod
    def _capture_ingestion_metadata(
        run_record: IngestionRun,
        db: Session,
        user_token: Optional[str] = None,
    ):
        """
        After successful ingestion, discover and cache metadata from Databricks
        target tables into DataOne's schema catalog.
        """
        logger.info(
            "[databricks_ingestion] stage=capture_metadata run_id=%s",
            run_record.id
        )
        
        try:
            from app.services.databricks_unity_catalog_service import UnityCatalogService
            from app.services.schema_catalog_service import SchemaCatalogService
            
            # Get target catalog/schema from trigger params
            params = run_record.trigger_params or {}
            target_catalog = params.get("dataone.target.catalog", "workspace")
            target_schema = params.get("dataone.target.schema", "default")
            
            # Get source database name to identify tables
            source_db = params.get("dataone.source.database", "")
            
            logger.info(
                "[databricks_ingestion] stage=discover_tables catalog=%s schema=%s",
                target_catalog, target_schema
            )
            
            # Initialize Unity Catalog service
            uc_service = UnityCatalogService(
                server_hostname=settings.DATABRICKS_HOST or "",
                http_path="",  # Not needed for Unity Catalog queries
                access_token=user_token or settings.DATABRICKS_ACCESS_TOKEN or "",
            )
            
            # Get all tables in the target schema
            tables = uc_service.get_tables(target_catalog, target_schema)
            
            # Filter tables that match source database prefix (e.g., source_db_tablename)
            prefix = f"{source_db}_" if source_db else ""
            ingested_tables = [
                t for t in tables 
                if prefix and t["name"].startswith(prefix)
            ] if prefix else tables
            
            logger.info(
                "[databricks_ingestion] stage=found_tables count=%d",
                len(ingested_tables)
            )
            
            # Create or get a Databricks connection for this catalog/schema
            # This allows us to associate the metadata with a connection
            databricks_conn = (
                db.query(DBConnection)
                .filter(
                    DBConnection.type == "databricks",
                    DBConnection.is_deleted == False
                )
                .first()
            )
            
            if not databricks_conn:
                # Create a default Databricks connection
                databricks_conn = DBConnection(
                    name=f"Databricks_{target_catalog}",
                    type="databricks",
                    environment="prod",
                    config={
                        "catalog": target_catalog,
                        "schema": target_schema,
                        "server_hostname": settings.DATABRICKS_HOST or "",
                    },
                    health_status="healthy",
                )
                db.add(databricks_conn)
                db.flush()
            
            # Store metadata for each table
            for table in ingested_tables:
                try:
                    table_name = f"{target_catalog}.{target_schema}.{table['name']}"
                    
                    # Get table metadata including columns
                    metadata = uc_service.get_table_metadata(
                        target_catalog, target_schema, table['name']
                    )
                    
                    # Convert to format expected by schema catalog
                    columns = [
                        {
                            "name": col["name"],
                            "type": col["type"],
                            "nullable": col.get("nullable", True),
                            "is_primary_key": False,  # Unity Catalog doesn't expose PKs
                            "ordinal_position": idx,
                        }
                        for idx, col in enumerate(metadata.get("columns", []))
                    ]
                    
                    # Store in catalog
                    SchemaCatalogService.store_table_metadata(
                        db=db,
                        connection_id=databricks_conn.id,
                        table_name=table_name,
                        columns=columns,
                        source_type="databricks_ingestion",
                    )
                    
                    logger.info(
                        "[databricks_ingestion] stage=metadata_stored table=%s cols=%d",
                        table_name, len(columns)
                    )
                    
                except Exception as table_err:
                    logger.warning(
                        "[databricks_ingestion] stage=table_metadata_failed table=%s: %s",
                        table.get("name"), table_err
                    )
            
            # Update run record with tables discovered
            run_record.rows_ingested = len(ingested_tables)
            db.commit()
            
            logger.info(
                "[databricks_ingestion] stage=capture_metadata_complete tables=%d",
                len(ingested_tables)
            )
            
        except Exception as e:
            logger.error(
                "[databricks_ingestion] stage=capture_metadata_failed: %s", e
            )
            raise

    @staticmethod
    def list_pipelines(db: Session) -> list:
        """List all pre-created ingestion pipelines from the catalog."""
        return [
            {
                "id": c.id,
                "source_type": c.source_type,
                "job_id": c.databricks_job_id,
                "job_name": c.job_name,
                "pipeline_type": c.pipeline_type,
                "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
            }
            for c in db.query(IngestionPipelineCatalog).all()
        ]

    @staticmethod
    def trigger_genie(
        question: str,
        db: Session,
        actor: str,
        space_id: Optional[str] = None,
        user_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Trigger Databricks Genie AI for natural language queries.
        """
        logger.info("[databricks_ingestion] stage=genie question_len=%d actor=%s", len(question), actor)

        effective_space_id = space_id or settings.DATABRICKS_GENIE_SPACE_ID
        if not effective_space_id:
            return {"error": "DATABRICKS_GENIE_SPACE_ID not configured", "answer": None}

        try:
            ws = _get_workspace_client(user_token)
            # Databricks Genie API (preview)
            genie = ws.genie
            conversation = genie.start_conversation_and_wait(
                space_id=effective_space_id,
                content=question,
            )
            message = conversation.messages[-1] if conversation.messages else None
            answer = None
            if message and message.attachments:
                for att in message.attachments:
                    if hasattr(att, "text") and att.text:
                        answer = att.text.content
                        break
                    elif hasattr(att, "query") and att.query:
                        answer = f"SQL: {att.query.query}"
                        break
            return {
                "conversation_id": conversation.conversation_id,
                "answer": answer or "No answer returned from Genie",
                "space_id": effective_space_id,
            }
        except Exception as e:
            logger.error("[databricks_ingestion] stage=genie_failed: %s", e)
            return {"error": str(e), "answer": None}


def _run_to_dict(run: IngestionRun) -> Dict[str, Any]:
    """Serialize an IngestionRun to a dict."""
    return {
        "id": run.id,
        "databricks_run_id": run.databricks_run_id,
        "databricks_job_id": run.databricks_job_id,
        "source_type": run.source_type,
        "source_connection_id": run.source_connection_id,
        "target_connection_id": run.target_connection_id,
        "status": run.status,
        "rows_ingested": run.rows_ingested,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
        "actor": run.actor,
        "databricks_run_url": run.databricks_run_url,
    }


# Global singleton
databricks_ingestion_service = DatabricksIngestionService()
