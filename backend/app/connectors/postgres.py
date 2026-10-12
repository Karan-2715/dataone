import time
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any
from .base import BaseConnector, ColumnProfileResult, TestConnectionResult, classify_connection_error

class PostgresConnector(BaseConnector):
    """
    Connector for PostgreSQL databases using psycopg2.
    """
    def __init__(self, host: str, port: int, dbname: str, user: str, password: str):
        self.config = {
            'host': host,
            'port': port,
            'dbname': dbname,
            'user': user,
            'password': password
        }
        self.conn = None

    def connect(self):
        if not self.conn:
            # Driver-level timeout slightly under the 5s API timeout so the
            # driver's own error message wins over a generic future-timeout.
            self.conn = psycopg2.connect(connect_timeout=4, **self.config)
        return self.conn

    def test_connection(self) -> TestConnectionResult:
        try:
            start = time.monotonic()
            conn = self.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
            cursor.close()
            latency = int((time.monotonic() - start) * 1000)
            return TestConnectionResult(
                success=True,
                version=version.split(" on ")[0] if version else None,
                latency_ms=latency,
            )
        except Exception as e:
            return classify_connection_error(str(e))

    def get_tables(self) -> List[str]:
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """)
        return [row[0] for row in cursor.fetchall()]

    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        conn = self.connect()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT column_name as name, data_type as type, is_nullable = 'YES' as nullable
            FROM information_schema.columns
            WHERE table_name = %s
        """, (table_name,))
        columns = cursor.fetchall()

        pk_cursor = conn.cursor()
        pk_cursor.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = 'public'
              AND tc.table_name = %s
            """,
            (table_name,),
        )
        pk_cols = {row[0] for row in pk_cursor.fetchall()}

        fk_cursor = conn.cursor()
        fk_cursor.execute(
            """
            SELECT kcu.column_name, ccu.table_name AS references_table,
                   ccu.column_name AS references_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public'
              AND tc.table_name = %s
            """,
            (table_name,),
        )
        fks_by_column: Dict[str, List[Dict[str, str]]] = {}
        for row in fk_cursor.fetchall():
            fks_by_column.setdefault(row[0], []).append({
                "references_table": row[1],
                "references_column": row[2],
            })

        schema = []
        for col in columns:
            schema.append({
                "name": col["name"],
                "type": col["type"],
                "nullable": col["nullable"],
                "primary_key": col["name"] in pk_cols,
                "foreign_keys": fks_by_column.get(col["name"], []),
            })
        return schema

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def profile_column(self, table: str, column: str,
                        sample_limit: int = 1000,
                        distinct_scan_limit: int = 100000) -> ColumnProfileResult:
        conn = self.connect()
        q = '"' + table.replace('"', '""') + '"'
        c = '"' + column.replace('"', '""') + '"'

        cursor = conn.cursor()
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
            conn.rollback()
            error = f"distinct count unavailable: {e}"

        try:
            cursor.execute(f"SELECT MIN({c}), MAX({c}) FROM {q}")
            min_val, max_val = cursor.fetchone()
        except Exception as e:
            conn.rollback()
            error = (error + "; " if error else "") + f"min/max unavailable: {e}"

        sample: List[Any] = []
        try:
            cursor.execute(
                f"SELECT {c} FROM {q} WHERE {c} IS NOT NULL LIMIT %s",
                (int(sample_limit),),
            )
            sample = [r[0] for r in cursor.fetchall()]
        except Exception:
            conn.rollback()  # Non-fatal — sample is best-effort

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
