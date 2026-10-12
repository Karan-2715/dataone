import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import List, Dict, Any

from app.core.config import settings
from app.connectors.base import TestConnectionResult
from app.connectors.sqlite import SQLiteConnector
from app.connectors.postgres import PostgresConnector
from app.connectors.mysql import MySQLConnector
from app.connectors.oracle import OracleConnector
from app.connectors.jdbc import JDBCConnector
from app.connectors.databricks_connector import DatabricksConnector
from app.models.connection import DBConnection

logger = logging.getLogger(__name__)


def get_connector(connection: DBConnection):
    """
    Factory function to initialize correct connector based on DBConnection model.

    Credentials are resolved through the SecretManager when the connection
    has a secrets_ref (keeperdb_integration_tasks #4) — a vault outage fails
    HERE with a clear error, so only credential-dependent operations break;
    metadata reads never pass through this function.
    """
    from app.services.connection_secrets_service import resolve_connection_config

    conn_type = connection.type.lower()
    config = resolve_connection_config(connection)

    if conn_type == "sqlite":
        if "path" not in config:
            raise ValueError("SQLite config must include 'path'")
        return SQLiteConnector(config["path"])
        
    elif conn_type == "postgres":
        required = ["host", "port", "dbname", "user", "password"]
        for r in required:
            if r not in config:
                raise ValueError(f"Postgres config must include '{r}'")
        return PostgresConnector(**config)

    elif conn_type == "mysql":
        required = ["host", "port", "dbname", "user", "password"]
        for r in required:
            if r not in config:
                raise ValueError(f"MySQL config must include '{r}'")
        return MySQLConnector(**config)

    elif conn_type == "oracle":
        required = ["host", "port", "service_name", "user", "password"]
        for r in required:
            if r not in config:
                raise ValueError(f"Oracle config must include '{r}'")
        return OracleConnector(**config)

    elif conn_type == "jdbc":
        if "url" not in config:
            raise ValueError("JDBC config must include 'url'")
        return JDBCConnector(url=config["url"], schema=config.get("schema"))
    
    elif conn_type == "databricks":
        required = ["server_hostname", "http_path", "access_token"]
        for r in required:
            if r not in config:
                raise ValueError(f"Databricks config must include '{r}'")
        return DatabricksConnector(
            server_hostname=config["server_hostname"],
            http_path=config["http_path"],
            access_token=config["access_token"],
            catalog=config.get("catalog"),
            schema=config.get("schema")
        )
        
    else:
        raise ValueError(f"Unsupported connector type: {conn_type}")

class SchemaService:
    @staticmethod
    def get_full_schema(connection: DBConnection) -> Dict[str, Any]:
        """
        Extracts full schema structure (tables and columns) from the connection.
        """
        # SUPER FAST PATH: Check if we have this connection's schema cached in SchemaSnapshot
        from app.core.database import SessionLocal
        from app.models.schema_snapshot import SchemaSnapshot
        from app.models.schema_catalog import CatalogTable
        with SessionLocal() as db:
            snapshot = db.query(SchemaSnapshot).filter(SchemaSnapshot.connection_id == connection.id).first()
            if snapshot and snapshot.schema_json:
                return dict(snapshot.schema_json)

            # FALLBACK FAST PATH: CatalogTable
            tables = db.query(CatalogTable).filter(CatalogTable.connection_id == connection.id).all()
            if tables:
                schema_data = {}
                for table in tables:
                    cols = [{"name": c.column_name, "type": c.data_type, "nullable": c.nullable} for c in table.columns]
                    schema_data[table.table_name] = cols
                return schema_data

        # SLOW PATH: Fallback to live extraction if not in DB
        connector = get_connector(connection)
        try:
            if connection.type.lower() == "databricks":
                # For Databricks Unity Catalog, the connection represents a foreign catalog that contains
                # multiple schemas (databases) from the source server. We must fetch all of them.
                schema_data = {}
                catalogs = connector.get_catalogs()
                SKIP_SCHEMAS = {
                    # Standard SQL / MySQL system schemas
                    "information_schema", "mysql", "performance_schema", "sys",
                    # Databricks internal
                    "__databricks_internal", "system",
                    # PostgreSQL system schemas (appear in federated/foreign catalogs)
                    "pg_catalog", "pg_toast", "pg_temp",
                }
                
                for cat in catalogs:
                    cat_name = cat["name"]
                    if cat_name.lower() in ("system",):  # Optionally skip system catalog
                        continue
                        
                    try:
                        schemas = connector.get_schemas(cat_name)
                    except Exception:
                        continue
                        
                    for sch in schemas:
                        sch_name = sch["name"]
                        if sch_name.lower() in SKIP_SCHEMAS:
                            continue
                            
                        # Create a temporary connector for this schema to fetch its tables
                        sch_connector = get_connector(connection)
                        sch_connector.config["schema"] = sch_name
                        sch_connector.config["catalog"] = cat_name
                        
                        try:
                            tables = sch_connector.get_tables()
                            # If a schema has no tables, we add a dummy table to show the schema in the UI
                            if len(tables) == 0:
                                schema_data[f"{cat_name}.{sch_name}.(empty_schema)"] = []
                            else:
                                for tbl in tables:
                                    # Ensure we prepend correctly to have catalog.schema.table
                                    if tbl.count(".") == 1:
                                        full_tbl = f"{cat_name}.{tbl}"
                                    elif tbl.count(".") == 0:
                                        full_tbl = f"{cat_name}.{sch_name}.{tbl}"
                                    else:
                                        full_tbl = tbl
                                    schema_data[full_tbl] = sch_connector.get_table_schema(tbl)
                        except Exception:
                            pass
                        finally:
                            sch_connector.close()
                return schema_data
            else:
                tables = connector.get_tables()
                schema_data = {}
                for table in tables:
                    schema_data[table] = connector.get_table_schema(table)
                return schema_data
        finally:
            connector.close()

    @staticmethod
    def test_connection(connection: DBConnection) -> TestConnectionResult:
        """Test connectivity with structured diagnostics and a hard timeout
        (connector_tasks #4, FR4 + performance NFR: result ≤ 5s or a clear
        timeout message). Never raises."""
        timeout = settings.CONNECTOR_TEST_TIMEOUT_SECONDS
        try:
            connector = get_connector(connection)
        except Exception as e:
            # Bad/missing config fields — reported as diagnostics, not a 500.
            return TestConnectionResult(
                success=False, reachable=False, authenticated=False,
                database_accessible=False,
                error_message=str(e), error_code="INVALID_CONFIG",
            )

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(connector.test_connection)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            logger.warning(
                "[connectors] stage=test connection_id=%s timed out after %ss",
                connection.id, timeout,
            )
            return TestConnectionResult(
                success=False, reachable=False, authenticated=False,
                database_accessible=False,
                error_message=f"Connection test timed out after {timeout} seconds",
                error_code="CONNECTION_TIMEOUT",
            )
        except Exception as e:
            # Drivers classify their own errors and shouldn't raise; if one
            # does anyway, report it as diagnostics instead of a 500.
            from app.connectors.base import classify_connection_error
            logger.error("[connectors] stage=test connection_id=%s driver raised: %s",
                         connection.id, e)
            return classify_connection_error(str(e))
        finally:
            executor.shutdown(wait=False)
            # The abandoned worker thread may still hold a socket — close the
            # connector so its FD is released as soon as connect() returns.
            try:
                connector.close()
            except Exception as e:
                logger.warning("[connectors] connector close after test failed: %s", e)
