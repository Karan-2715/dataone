"""
Unity Catalog integration service for Databricks connections.

This service DELEGATES catalog, lineage, and governance features to Unity Catalog
(per the assessment: ~45% of features delegated to Databricks primitives).

Multi-source fallback: For non-Databricks connections, the app-owned catalog
service remains active (preserving the multi-source capability).

OAuth Mode: When running as a Databricks Lakehouse App, this service uses
OAuth tokens from authenticated users instead of PAT tokens, respecting
Unity Catalog permissions at the user level.
"""
import logging
from typing import List, Dict, Any, Optional
from databricks import sql
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config

from app.models.connection import DBConnection
from app.core.config import settings

logger = logging.getLogger(__name__)


class UnityCatalogService:
    """
    Service for Unity Catalog metadata, lineage, and governance operations.
    
    DELEGATE features (from assessment):
    - Schema catalog & profiling (UC information_schema)
    - Data lineage (UC automatic lineage)
    - PII/classification (UC tags + auto-classification)
    - RBAC/masking (UC grants, row filters, masks)
    
    OAuth Mode:
    - When DATABRICKS_NATIVE_MODE=true, uses user's OAuth token
    - Respects user-level Unity Catalog permissions
    - Automatically refreshes tokens when needed
    """
    
    def __init__(
        self, 
        connection: DBConnection,
        user_access_token: Optional[str] = None
    ):
        """
        Initialize UC service for a Databricks connection.
        
        Args:
            connection: DBConnection model with Databricks credentials
            user_access_token: Optional OAuth token from authenticated user
                              (used in Databricks native mode)
        """
        if connection.type.lower() != "databricks":
            raise ValueError(
                f"UnityCatalogService requires a Databricks connection, got: {connection.type}"
            )
        
        self.connection = connection
        self.config = connection.config or {}
        
        # In Databricks native mode, prefer user's OAuth token
        self.is_native_mode = getattr(settings, "DATABRICKS_NATIVE_MODE", False)
        self.access_token = user_access_token if self.is_native_mode else self.config.get("access_token")
        
        if not self.access_token:
            logger.warning(
                "No access token provided for Unity Catalog service. "
                "Using connection config token as fallback."
            )
            self.access_token = self.config.get("access_token")
        
        # Initialize SQL connection for queries
        self.sql_conn = None
        
        # Initialize SDK client for UC APIs
        self.workspace_client: Optional[WorkspaceClient] = None
        
        logger.info(
            f"UnityCatalogService initialized in {'OAuth' if self.is_native_mode else 'PAT'} mode"
        )
    
    def _get_sql_connection(self):
        """Get or create SQL connection using appropriate token."""
        if not self.sql_conn:
            self.sql_conn = sql.connect(
                server_hostname=self.config.get("server_hostname"),
                http_path=self.config.get("http_path"),
                access_token=self.access_token  # Use OAuth token in native mode
            )
        return self.sql_conn
    
    def _get_workspace_client(self) -> WorkspaceClient:
        """Get or create Workspace SDK client for UC APIs.

        On Databricks Apps, OAuth M2M credentials are injected via env vars
        (DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET, DATABRICKS_HOST).
        Passing an explicit PAT token alongside those causes the SDK to raise
        'more than one authorization method configured'.
        We detect this and let the SDK self-configure from the environment.
        """
        if not self.workspace_client:
            import os
            is_databricks_apps = bool(
                os.environ.get("DATABRICKS_CLIENT_ID")
                or os.environ.get("DATABRICKS_CLIENT_SECRET")
            )
            if is_databricks_apps:
                logger.info("[UC] Databricks Apps environment detected — using OAuth M2M from env")
                self.workspace_client = WorkspaceClient()
            else:
                config = Config(
                    host=f"https://{self.config.get('server_hostname')}",
                    token=self.access_token
                )
                self.workspace_client = WorkspaceClient(config=config)
        return self.workspace_client


    def update_access_token(self, new_token: str):
        """
        Update the access token (e.g., after refresh).
        
        This forces reconnection with the new token on next API call.
        
        Args:
            new_token: New OAuth access token
        """
        self.access_token = new_token
        
        # Close existing connections to force reconnect with new token
        self.close()
        
        logger.info("Access token updated, connections will reconnect")
    
    def _execute_spark_python(self, python_code: str) -> Any:
        """
        Executes a Spark Python snippet on an existing interactive cluster
        using the Command Execution API and returns the parsed JSON.
        """
        import json
        import time
        from databricks.sdk.service import compute
        
        wc = self._get_workspace_client()
        
        # Find a running cluster
        clusters = wc.clusters.list()
        running_clusters = [c for c in clusters if c.state == compute.State.RUNNING and c.cluster_source != compute.ClusterSource.JOB]
        if not running_clusters:
            running_clusters = [c for c in clusters if c.state == compute.State.RUNNING]
            
        if not running_clusters:
            raise Exception("No running Databricks clusters available for Spark execution.")
            
        cluster_id = running_clusters[0].cluster_id
        
        # Create execution context
        context = wc.command_execution.create(
            cluster_id=cluster_id,
            language=compute.Language.PYTHON
        )
        context_id = context.id
        
        try:
            # Execute command
            cmd = wc.command_execution.execute(
                cluster_id=cluster_id,
                context_id=context_id,
                language=compute.Language.PYTHON,
                command=python_code
            )
            
            # Wait for it to finish
            command_id = cmd.id
            status = cmd.status
            
            while status == compute.CommandStatus.RUNNING or status == compute.CommandStatus.QUEUED:
                time.sleep(2)
                cmd_status = wc.command_execution.command_status(
                    cluster_id=cluster_id,
                    context_id=context_id,
                    command_id=command_id
                )
                status = cmd_status.status
                if status not in (compute.CommandStatus.RUNNING, compute.CommandStatus.QUEUED):
                    cmd = cmd_status
                    break
                    
            if status == compute.CommandStatus.FINISHED:
                output_str = cmd.results.data
                if cmd.results.result_type == compute.ResultType.ERROR:
                    raise Exception(f"Spark Python error: {cmd.results.summary}")
                    
                try:
                    # Find the last valid JSON line
                    lines = output_str.strip().split('\n')
                    for line in reversed(lines):
                        try:
                            res = json.loads(line)
                            if isinstance(res, dict) and "_error" in res:
                                raise Exception(res["_error"])
                            return res
                        except ValueError:
                            continue
                    return json.loads(output_str)
                except Exception as parse_e:
                    logger.error(f"Failed to parse Spark Python output: {output_str}")
                    raise Exception(f"Invalid JSON returned from Spark: {parse_e}")
            else:
                raise Exception(f"Spark execution failed: {cmd.results.summary}")
        finally:
            try:
                wc.command_execution.destroy(cluster_id=cluster_id, context_id=context_id)
            except:
                pass

    def get_catalogs(self) -> List[Dict[str, Any]]:
        """
        Get all Unity Catalogs the user has access to via Spark Python code.
        """
        try:
            code = """
import json
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
try:
    catalogs = spark.catalog.listCatalogs()
    results = [
        {
            "name": c.name,
            "comment": c.description,
            "type": "unity_catalog"
        }
        for c in catalogs
    ]
    print(json.dumps(results))
except Exception as e:
    print(json.dumps({"_error": str(e)}))
"""
            return self._execute_spark_python(code)
        except Exception as e:
            logger.error("Failed to list Unity Catalogs via Spark: %s", e)
            return []
    
    def get_schemas(self, catalog_name: str) -> List[Dict[str, Any]]:
        """
        Get all schemas in a Unity Catalog via Spark Python code.
        """
        try:
            code = f"""
import json
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
try:
    schemas = spark.catalog.listDatabases("{catalog_name}")
    results = [
        {{
            "name": s.name,
            "catalog": "{catalog_name}",
            "comment": s.description,
            "full_name": f"{catalog_name}.{{s.name}}"
        }}
        for s in schemas
    ]
    print(json.dumps(results))
except Exception as e:
    print(json.dumps({{"_error": str(e)}}))
"""
            return self._execute_spark_python(code)
        except Exception as e:
            logger.error("Failed to list schemas in catalog %s via Spark: %s", catalog_name, e)
            return []
    
    def get_tables(self, catalog_name: str, schema_name: str) -> List[Dict[str, Any]]:
        """
        Get all tables in a Unity Catalog schema via Spark Python code.
        """
        try:
            code = f"""
import json
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
try:
    tables = spark.catalog.listTables(f"{catalog_name}.{schema_name}")
    results = [
        {{
            "name": t.name,
            "catalog": "{catalog_name}",
            "schema": "{schema_name}",
            "full_name": f"{catalog_name}.{schema_name}.{{t.name}}",
            "table_type": t.tableType,
            "comment": t.description
        }}
        for t in tables
    ]
    print(json.dumps(results))
except Exception as e:
    print(json.dumps({{"_error": str(e)}}))
"""
            return self._execute_spark_python(code)
        except Exception as e:
            logger.error(
                "Failed to list tables in %s.%s via Spark: %s",
                catalog_name, schema_name, e
            )
            return []
    
    def get_table_metadata(
        self, 
        catalog_name: str, 
        schema_name: str, 
        table_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get detailed table metadata from Unity Catalog via Spark Python code.
        """
        try:
            code = f"""
import json
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
try:
    full_name = f"{catalog_name}.{schema_name}.{table_name}"
    columns = spark.catalog.listColumns(full_name)
    
    col_results = []
    for i, c in enumerate(columns):
        col_results.append({{
            "name": c.name,
            "type_name": c.dataType,
            "type_text": c.dataType,
            "position": i,
            "comment": c.description,
            "nullable": c.nullable,
            "partition_index": -1
        }})
        
    result = {{
        "full_name": full_name,
        "name": "{table_name}",
        "catalog": "{catalog_name}",
        "schema": "{schema_name}",
        "columns": col_results,
        "properties": {{}}
    }}
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({{"_error": str(e)}}))
"""
            return self._execute_spark_python(code)
        except Exception as e:
            logger.error("Failed to get table metadata for %s.%s.%s via Spark: %s", catalog_name, schema_name, table_name, e)
            return None
    
    def get_table_lineage(
        self,
        catalog_name: str,
        schema_name: str,
        table_name: str
    ) -> Dict[str, Any]:
        """
        Get automatic lineage from Unity Catalog.
        
        DELEGATE: Replaces app-owned lineage tracking (catalog snapshots/diff).
        This is a major efficiency win - UC tracks lineage automatically.
        """
        try:
            wc = self._get_workspace_client()
            full_name = f"{catalog_name}.{schema_name}.{table_name}"
            
            # Get lineage using UC API
            lineage = wc.lineage.get_by_table(table_name=full_name)
            
            upstream_tables = []
            downstream_tables = []
            
            if lineage.upstreams:
                for upstream in lineage.upstreams:
                    upstream_tables.append({
                        "table_name": upstream.table_info.name if upstream.table_info else None,
                        "full_name": upstream.table_info.full_name if upstream.table_info else None,
                        "catalog": upstream.table_info.catalog_name if upstream.table_info else None,
                        "schema": upstream.table_info.schema_name if upstream.table_info else None
                    })
            
            if lineage.downstreams:
                for downstream in lineage.downstreams:
                    downstream_tables.append({
                        "table_name": downstream.table_info.name if downstream.table_info else None,
                        "full_name": downstream.table_info.full_name if downstream.table_info else None,
                        "catalog": downstream.table_info.catalog_name if downstream.table_info else None,
                        "schema": downstream.table_info.schema_name if downstream.table_info else None
                    })
            
            return {
                "table": full_name,
                "upstream": upstream_tables,
                "downstream": downstream_tables,
                "source": "unity_catalog_automatic"
            }
            
        except Exception as e:
            logger.warning("Lineage not available for %s.%s.%s: %s", 
                         catalog_name, schema_name, table_name, e)
            return {
                "table": f"{catalog_name}.{schema_name}.{table_name}",
                "upstream": [],
                "downstream": [],
                "source": "unavailable",
                "error": str(e)
            }
    
    def get_table_tags(
        self,
        catalog_name: str,
        schema_name: str,
        table_name: str
    ) -> Dict[str, str]:
        """
        Get Unity Catalog tags (includes auto-classified PII).
        
        DELEGATE: Replaces DAMA classifier for Databricks sources.
        UC auto-classification is more accurate and up-to-date.
        """
        try:
            wc = self._get_workspace_client()
            full_name = f"{catalog_name}.{schema_name}.{table_name}"
            table = wc.tables.get(full_name)
            
            return table.tags or {}
            
        except Exception as e:
            logger.warning("Could not get tags for %s: %s", full_name, e)
            return {}
    
    def get_column_tags(
        self,
        catalog_name: str,
        schema_name: str,
        table_name: str,
        column_name: str
    ) -> Dict[str, str]:
        """
        Get Unity Catalog column-level tags (PII classification).
        
        DELEGATE: Column-level PII detection from UC.
        """
        try:
            wc = self._get_workspace_client()
            full_name = f"{catalog_name}.{schema_name}.{table_name}"
            table = wc.tables.get(full_name)
            
            # Find the column
            for col in (table.columns or []):
                if col.name == column_name:
                    return col.tags or {}
            
            return {}
            
        except Exception as e:
            logger.warning(
                "Could not get column tags for %s.%s: %s",
                full_name, column_name, e
            )
            return {}
    
    def get_table_grants(
        self,
        catalog_name: str,
        schema_name: str,
        table_name: str
    ) -> List[Dict[str, Any]]:
        """
        Get access grants for a table.
        
        DELEGATE: RBAC from Unity Catalog grants.
        """
        try:
            wc = self._get_workspace_client()
            full_name = f"{catalog_name}.{schema_name}.{table_name}"
            
            grants = wc.grants.get(securable_type="TABLE", full_name=full_name)
            
            grant_list = []
            for assignment in (grants.privilege_assignments or []):
                grant_list.append({
                    "principal": assignment.principal,
                    "privileges": assignment.privileges or []
                })
            
            return grant_list
            
        except Exception as e:
            logger.warning("Could not get grants for %s: %s", full_name, e)
            return []
    
    def search_tables(
        self,
        query: str,
        max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Search for tables across Unity Catalog using metadata.
        
        Args:
            query: Search query string
            max_results: Maximum number of results
            
        Returns:
            List of matching tables with metadata
        """
        conn = self._get_sql_connection()
        cursor = conn.cursor()
        
        try:
            # Search using information_schema
            sql_query = """
                SELECT 
                    table_catalog,
                    table_schema,
                    table_name,
                    table_type,
                    comment
                FROM system.information_schema.tables
                WHERE LOWER(table_name) LIKE LOWER(?)
                   OR LOWER(comment) LIKE LOWER(?)
                ORDER BY table_catalog, table_schema, table_name
                LIMIT ?
            """
            
            search_pattern = f"%{query}%"
            cursor.execute(sql_query, (search_pattern, search_pattern, max_results))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "catalog": row[0],
                    "schema": row[1],
                    "name": row[2],
                    "full_name": f"{row[0]}.{row[1]}.{row[2]}",
                    "type": row[3],
                    "comment": row[4]
                })
            
            return results
            
        finally:
            cursor.close()
    
    def close(self):
        """Close connections."""
        if self.sql_conn:
            try:
                self.sql_conn.close()
            except Exception as e:
                logger.warning("Error closing SQL connection: %s", e)
            finally:
                self.sql_conn = None
        
        # Workspace client doesn't need explicit cleanup
        self.workspace_client = None


def get_unity_catalog_service_for_user(
    connection: DBConnection,
    user=None,
    db_session=None
) -> UnityCatalogService:
    """
    Factory function to create Unity Catalog service.
    
    DataOne always uses its own Service Principal (M2M) token 
    instead of the user's OAuth token to prevent 'Invalid scope' 
    errors with Unity Catalog APIs.
    """
    logger.info("Creating UC service with DataOne Service Principal (M2M)")
    return UnityCatalogService(connection)
