"""Pytest fixtures for the Schema Topology graph test suite.

Mirrors tests/pipelines/conftest.py's seeded_mapping_with_field_mappings
pattern — a real published mapping renaming users -> customers with
column renames too, which is exactly the scenario the topology graph's
exact-name matching got wrong (bug: every renamed table was flagged
"not found in target schema" despite a real, working mapping existing).
"""
from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")
os.environ.setdefault("OLLAMA_MODEL", "llama3")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "True")
os.environ.setdefault("SECRET_KEY", "test-secret")


def _install_driver_stubs() -> None:
    drivers = ("psycopg2", "pymysql", "oracledb", "mysql", "pgdb")
    sub_modules = {
        "psycopg2": ("extras", "pool", "sql", "extensions"),
        "pymysql": ("connections", "cursors", "err"),
        "oracledb": ("errors",),
    }
    for name in drivers:
        if name in sys.modules:
            continue
        mod = types.ModuleType(name)
        mod.__path__ = []
        sys.modules[name] = mod
        for sub in sub_modules.get(name, ()):
            full = f"{name}.{sub}"
            if full not in sys.modules:
                sys.modules[full] = types.ModuleType(full)

    class _Stub:
        pass

    def _add(module_name: str, attrs: dict) -> None:
        mod = sys.modules.get(module_name)
        if mod is None:
            return
        for k, v in attrs.items():
            if not hasattr(mod, k):
                setattr(mod, k, v)

    _add("psycopg2.extras", {"RealDictCursor": _Stub, "NamedTupleCursor": _Stub, "DictCursor": _Stub})
    _add("pymysql.cursors", {"DictCursor": _Stub, "Cursor": _Stub, "SSDictCursor": _Stub})
    _add("pymysql.connections", {"Connection": _Stub})
    _add("oracledb.errors", {"DatabaseError": _Stub, "IntegrityError": _Stub, "OperationalError": _Stub})


_install_driver_stubs()

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.database import Base  # noqa: E402
from app.models.connection import DBConnection  # noqa: E402
from app.models.mapping import FieldMapping, Mapping, MappingVersion  # noqa: E402


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        Base.metadata.drop_all(eng)
        eng.dispose()


@pytest.fixture()
def db(engine):
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def physical_sqlite_connections(db, tmp_path):
    """Real SQLite files with a source 'users' table and a target
    'customers' table — a genuine table rename, same shape as the
    crm_users -> dw_customers scenario from the bug report."""
    import sqlite3

    src_path = str(tmp_path / "topo_src.db")
    tgt_path = str(tmp_path / "topo_tgt.db")

    src_conn = sqlite3.connect(src_path)
    src_conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    src_conn.execute("CREATE TABLE leads (id INTEGER PRIMARY KEY, company TEXT)")
    src_conn.commit()
    src_conn.close()

    tgt_conn = sqlite3.connect(tgt_path)
    tgt_conn.execute("CREATE TABLE customers (cust_id INTEGER PRIMARY KEY, full_name TEXT, contact_email TEXT)")
    tgt_conn.execute("CREATE TABLE opportunities (opp_id INTEGER PRIMARY KEY, organization TEXT)")
    tgt_conn.commit()
    tgt_conn.close()

    src = DBConnection(name="TopoSrc", type="sqlite", config={"path": src_path})
    tgt = DBConnection(name="TopoTgt", type="sqlite", config={"path": tgt_path})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)
    return src, tgt


@pytest.fixture()
def seeded_mapping_with_field_mappings(db, physical_sqlite_connections):
    """A published Mapping + MappingVersion + real FieldMapping rows
    (version_id set) mapping users.id/name/email ->
    customers.cust_id/full_name/contact_email — 'leads' is deliberately
    left unmapped so tests can assert it's still correctly flagged."""
    from app.connectors.sqlite import SQLiteConnector

    src, tgt = physical_sqlite_connections
    src_connector = SQLiteConnector(src.config["path"])
    tgt_connector = SQLiteConnector(tgt.config["path"])
    source_schema = {t: src_connector.get_table_schema(t) for t in src_connector.get_tables()}
    target_schema = {t: tgt_connector.get_table_schema(t) for t in tgt_connector.get_tables()}
    src_connector.close()
    tgt_connector.close()

    m = Mapping(name="Topo Map", source_id=src.id, target_id=tgt.id,
                status="published", created_by="test")
    db.add(m)
    db.flush()
    v = MappingVersion(
        mapping_id=m.id, version_number=1, status="published", published_by="test",
        schema_snapshot={"source": source_schema, "target": target_schema},
        edges_snapshot=[],
    )
    db.add(v)
    db.flush()

    field_mappings = [
        ("id", "cust_id", True),
        ("name", "full_name", False),
        ("email", "contact_email", False),
    ]
    for source_col, target_col, is_pk in field_mappings:
        db.add(FieldMapping(
            mapping_id=m.id, version_id=v.id,
            target_table="customers", target_column=target_col,
            target_is_pk=1 if is_pk else 0,
            sources=[{"table": "users", "column": source_col, "type": "TEXT"}],
            transformation={"kind": "direct"},
            origin="manual",
        ))

    m.current_version_id = v.id
    db.commit()
    db.refresh(m)
    return m, v
