import time
import pymysql
from pymysql.cursors import Cursor, DictCursor
from typing import List, Dict, Any
from .base import BaseConnector, ColumnProfileResult, TestConnectionResult, classify_connection_error


class MySQLConnector(BaseConnector):
    """
    Connector for MySQL databases using PyMySQL.
    """

    def __init__(self, host: str, port: int, dbname: str, user: str, password: str):
        self.config = {
            "host": host,
            "port": int(port),
            "database": dbname,
            "user": user,
            "password": password,
        }
        self.conn = None

    def connect(self):
        if not self.conn:
            # Driver-level timeout slightly under the 5s API timeout so the
            # driver's own error message wins over a generic future-timeout.
            self.conn = pymysql.connect(**self.config, cursorclass=DictCursor,
                                        connect_timeout=4)
        return self.conn

    def test_connection(self) -> TestConnectionResult:
        try:
            start = time.monotonic()
            conn = self.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT VERSION() AS v")
            version = cursor.fetchone()["v"]
            latency = int((time.monotonic() - start) * 1000)
            return TestConnectionResult(
                success=True,
                version=f"MySQL {version}" if version else None,
                latency_ms=latency,
            )
        except Exception as e:
            return classify_connection_error(str(e))

    def get_tables(self) -> List[str]:
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        return [list(row.values())[0] for row in cursor.fetchall()]

    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                c.COLUMN_NAME   AS name,
                c.DATA_TYPE     AS type,
                c.IS_NULLABLE   AS nullable,
                c.COLUMN_KEY    AS col_key,
                k.REFERENCED_TABLE_NAME  AS ref_table,
                k.REFERENCED_COLUMN_NAME AS ref_column
            FROM information_schema.COLUMNS c
            LEFT JOIN information_schema.KEY_COLUMN_USAGE k
              ON c.TABLE_SCHEMA = k.TABLE_SCHEMA
             AND c.TABLE_NAME = k.TABLE_NAME
             AND c.COLUMN_NAME = k.COLUMN_NAME
             AND k.REFERENCED_TABLE_NAME IS NOT NULL
            WHERE c.TABLE_SCHEMA = DATABASE()
              AND c.TABLE_NAME   = %s
            ORDER BY c.ORDINAL_POSITION
        """, (table_name,))
        rows = cursor.fetchall()
        # A LEFT JOIN to KEY_COLUMN_USAGE can return >1 row per column if the
        # column participates in more than one FK constraint; group by name
        # so the column list itself never duplicates, only its foreign_keys.
        schema_by_name: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for r in rows:
            name = r["name"]
            if name not in schema_by_name:
                order.append(name)
                schema_by_name[name] = {
                    "name": name,
                    "type": r["type"],
                    "nullable": r["nullable"] == "YES",
                    "primary_key": r["col_key"] == "PRI",
                    "foreign_keys": [],
                }
            if r.get("ref_table"):
                schema_by_name[name]["foreign_keys"].append({
                    "references_table": r["ref_table"],
                    "references_column": r["ref_column"],
                })
        return [schema_by_name[n] for n in order]

    def execute_query(self, sql: str) -> List[Dict[str, Any]]:
        """Execute a read-only query and return results."""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(sql)
        return cursor.fetchall()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def profile_column(self, table: str, column: str,
                        sample_limit: int = 1000,
                        distinct_scan_limit: int = 100000) -> ColumnProfileResult:
        conn = self.connect()
        q = "`" + table.replace("`", "``") + "`"
        c = "`" + column.replace("`", "``") + "`"
        # Tuple cursor (not the connection's default DictCursor) so the
        # aggregate rows below unpack positionally.
        cursor = conn.cursor(Cursor)

        cursor.execute(f"SELECT COUNT(*), COUNT({c}) FROM {q}")
        total, non_null = cursor.fetchone()
        null_count = total - non_null
        null_rate = null_count / total if total > 0 else 0.0

        distinct_count = None
        min_val = max_val = None
        error = None
        try:
            cursor.execute(
                f"SELECT COUNT(DISTINCT {c}) FROM "
                f"(SELECT {c} FROM {q} LIMIT {int(distinct_scan_limit)}) sub"
            )
            distinct_count = cursor.fetchone()[0]
        except Exception as e:
            error = f"distinct count unavailable: {e}"

        try:
            cursor.execute(f"SELECT MIN({c}), MAX({c}) FROM {q}")
            min_val, max_val = cursor.fetchone()
        except Exception as e:
            error = (error + "; " if error else "") + f"min/max unavailable: {e}"

        sample: List[Any] = []
        try:
            cursor.execute(
                f"SELECT {c} FROM {q} WHERE {c} IS NOT NULL LIMIT %s",
                (int(sample_limit),),
            )
            sample = [r[0] for r in cursor.fetchall()]
        except Exception:
            pass  # Non-fatal — sample is best-effort

        return ColumnProfileResult(
            null_count=null_count,
            null_rate=null_rate,
            distinct_count=distinct_count,
            min_value=str(min_val) if min_val is not None else None,
            max_value=str(max_val) if max_val is not None else None,
            sample_values=sample,
            sample_size_used=len(sample),
            row_count=total,
            error=error,
        )
