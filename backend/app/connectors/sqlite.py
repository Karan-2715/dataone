import os
import sqlite3
import time
from typing import List, Dict, Any
from .base import BaseConnector, ColumnProfileResult, TestConnectionResult, classify_connection_error

class SQLiteConnector(BaseConnector):
    """
    Connector for local SQLite databases.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None

    def connect(self) -> sqlite3.Connection:
        if not self.conn:
            # check_same_thread=False: test_connection runs inside a timeout
            # worker thread while close() happens on the request thread.
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def test_connection(self) -> TestConnectionResult:
        # sqlite3.connect() happily creates a missing file, which would make
        # a typo'd path "pass" — check existence up front instead.
        if not os.path.exists(self.db_path):
            return TestConnectionResult(
                success=False, reachable=False, authenticated=False,
                database_accessible=False,
                error_message=f"SQLite file not found: {self.db_path}",
                error_code="CONNECTION_REFUSED",
            )
        try:
            start = time.monotonic()
            conn = self.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT sqlite_version()")
            version = cursor.fetchone()[0]
            latency = int((time.monotonic() - start) * 1000)
            return TestConnectionResult(
                success=True, version=f"SQLite {version}", latency_ms=latency,
            )
        except Exception as e:
            return classify_connection_error(str(e))

    def get_tables(self) -> List[str]:
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        return [row["name"] for row in cursor.fetchall()]

    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        conn = self.connect()
        cursor = conn.cursor()
        # PRAGMA can't take bound parameters — quote the identifier instead
        # (connector_tasks/bugs #03 defect class: no raw interpolation in SQL).
        quoted = '"' + table_name.replace('"', '""') + '"'
        cursor.execute(f"PRAGMA table_info({quoted})")
        columns = cursor.fetchall()

        fk_cursor = conn.cursor()
        fk_cursor.execute(f"PRAGMA foreign_key_list({quoted})")
        fks_by_column: Dict[str, List[Dict[str, str]]] = {}
        for r in fk_cursor.fetchall():
            fks_by_column.setdefault(r["from"], []).append({
                "references_table": r["table"],
                "references_column": r["to"],
            })

        schema = []
        for col in columns:
            schema.append({
                "name": col["name"],
                "type": col["type"],
                "nullable": col["notnull"] == 0,
                "primary_key": col["pk"] == 1,
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
                f"(SELECT {c} FROM {q} LIMIT {int(distinct_scan_limit)})"
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
            sample_cursor = conn.execute(
                f"SELECT {c} FROM {q} WHERE {c} IS NOT NULL LIMIT ?",
                (int(sample_limit),),
            )
            sample = [r[0] for r in sample_cursor.fetchall()]
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
