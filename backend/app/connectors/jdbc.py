"""
Generic JDBC-style Connector.

Uses SQLAlchemy as a universal abstraction to support any database that
provides a Python DB-API 2.0 driver or SQLAlchemy dialect URL.
"""

import time
from sqlalchemy import create_engine, inspect, text
from typing import List, Dict, Any
from .base import BaseConnector, ColumnProfileResult, TestConnectionResult, classify_connection_error


class JDBCConnector(BaseConnector):
    """
    Generic connector that accepts a SQLAlchemy-compatible connection URL.
    Works with any dialect: postgresql, mysql, sqlite, mssql, etc.

    Config expects: {"url": "dialect+driver://user:pass@host:port/dbname"}
    """

    def __init__(self, url: str, schema: str = None):
        self.url = url
        self.schema = schema
        self.engine = None
        self.conn = None

    def connect(self):
        if not self.engine:
            self.engine = create_engine(self.url, pool_pre_ping=True)
        if not self.conn:
            self.conn = self.engine.connect()
        return self.conn

    def test_connection(self) -> TestConnectionResult:
        try:
            start = time.monotonic()
            conn = self.connect()
            result = conn.execute(text("SELECT 1"))
            ok = result.scalar() == 1
            latency = int((time.monotonic() - start) * 1000)
            if not ok:
                return TestConnectionResult(
                    success=False, reachable=True,
                    database_accessible=False,
                    error_message="SELECT 1 returned an unexpected result",
                    error_code="UNKNOWN_ERROR",
                )
            dialect = self.engine.dialect
            server_info = getattr(dialect, "server_version_info", None)
            version = (f"{dialect.name} "
                       + ".".join(str(p) for p in server_info)) if server_info else dialect.name
            return TestConnectionResult(
                success=True, version=version, latency_ms=latency,
            )
        except Exception as e:
            return classify_connection_error(str(e))

    def get_tables(self) -> List[str]:
        self.connect()
        insp = inspect(self.engine)
        return insp.get_table_names(schema=self.schema)

    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        self.connect()
        insp = inspect(self.engine)
        columns = insp.get_columns(table_name, schema=self.schema)
        pk_cols = set()
        try:
            pk = insp.get_pk_constraint(table_name, schema=self.schema)
            pk_cols = set(pk.get("constrained_columns", []))
        except Exception:
            pass

        fks_by_column: Dict[str, List[Dict[str, str]]] = {}
        try:
            for fk in insp.get_foreign_keys(table_name, schema=self.schema):
                referred_table = fk.get("referred_table")
                for local_col, remote_col in zip(
                    fk.get("constrained_columns", []),
                    fk.get("referred_columns", []),
                ):
                    fks_by_column.setdefault(local_col, []).append({
                        "references_table": referred_table,
                        "references_column": remote_col,
                    })
        except Exception:
            pass

        schema_list = []
        for col in columns:
            schema_list.append({
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "primary_key": col["name"] in pk_cols,
                "foreign_keys": fks_by_column.get(col["name"], []),
            })
        return schema_list

    def execute_query(self, sql: str) -> List[Dict[str, Any]]:
        conn = self.connect()
        result = conn.execute(text(sql))
        keys = list(result.keys())
        return [dict(zip(keys, row)) for row in result.fetchall()]

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
        if self.engine:
            self.engine.dispose()
            self.engine = None

    def profile_column(self, table: str, column: str,
                        sample_limit: int = 1000,
                        distinct_scan_limit: int = 100000) -> ColumnProfileResult:
        conn = self.connect()
        q = f'"{table}"'
        c = f'"{column}"'

        total, non_null = conn.execute(text(f"SELECT COUNT(*), COUNT({c}) FROM {q}")).fetchone()
        null_count = total - non_null
        null_rate = null_count / total if total > 0 else 0.0

        distinct_count = None
        min_val = max_val = None
        error = None
        try:
            distinct_count = conn.execute(text(
                f"SELECT COUNT(DISTINCT {c}) FROM "
                f"(SELECT {c} FROM {q} LIMIT {int(distinct_scan_limit)}) sub"
            )).scalar()
        except Exception as e:
            error = f"distinct count unavailable: {e}"

        try:
            min_val, max_val = conn.execute(text(f"SELECT MIN({c}), MAX({c}) FROM {q}")).fetchone()
        except Exception as e:
            error = (error + "; " if error else "") + f"min/max unavailable: {e}"

        sample: List[Any] = []
        try:
            result = conn.execute(text(
                f"SELECT {c} FROM {q} WHERE {c} IS NOT NULL LIMIT :lim"
            ), {"lim": int(sample_limit)})
            sample = [r[0] for r in result.fetchall()]
        except Exception:
            pass

        return ColumnProfileResult(
            null_count=null_count, null_rate=null_rate,
            distinct_count=distinct_count,
            min_value=str(min_val) if min_val is not None else None,
            max_value=str(max_val) if max_val is not None else None,
            sample_values=sample, sample_size_used=len(sample),
            row_count=total, error=error,
        )
