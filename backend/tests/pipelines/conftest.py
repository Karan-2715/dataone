"""Pytest fixtures for the Pipelines test suite (Task #1).

Provides:
- engine: in-memory SQLite engine with Base.metadata.create_all (so the
  new pipeline tables exist alongside the existing mapping/connection
  tables).
- db: a Session bound to that engine.
- admin / analyst / viewer: User fixtures.
- seeded_connections: a pair of DBConnection rows (source + target).
- seeded_published_mapping: a Mapping with a published MappingVersion
  whose schema_snapshot is populated — needed by create_pipeline.
"""
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

# Install stubs for optional DB drivers BEFORE importing any app modules.
# app.connectors.postgres / mysql / oracle eagerly `import psycopg2` /
# `pymysql` / `oracledb` at module load even when those backends are never
# used. Tests run against SQLite only, so stub them out. This mirrors the
# pattern already in tests/mapping/conftest.py.
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

    def _add(module_name, attrs):
        mod = sys.modules.get(module_name)
        if mod is None:
            return
        for k, v in attrs.items():
            if not hasattr(mod, k):
                setattr(mod, k, v)

    _add("psycopg2.extras", {
        "RealDictCursor": _Stub, "NamedTupleCursor": _Stub, "DictCursor": _Stub,
    })
    _add("pymysql.cursors", {"DictCursor": _Stub, "Cursor": _Stub, "SSDictCursor": _Stub})
    _add("pymysql.connections", {"Connection": _Stub})
    _add("oracledb.errors", {
        "DatabaseError": _Stub, "IntegrityError": _Stub, "OperationalError": _Stub,
    })


_install_driver_stubs()

import json  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.database import Base  # noqa: E402
from app.models.connection import DBConnection  # noqa: E402
from app.models.mapping import Mapping, MappingVersion  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


@pytest.fixture()
def engine():
    # StaticPool + check_same_thread=False lets the in-memory SQLite engine be
    # shared safely across threads (FastAPI's TestClient runs request handlers
    # on an asyncio thread pool, not the test thread) — same pattern as
    # tests/mapping/conftest.py, needed by the router-level tests here too.
    from sqlalchemy.pool import StaticPool
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
def admin(db):
    u = User(
        email="admin@test.local",
        hashed_password=AuthService.hash_password("x"),
        role="admin",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def analyst(db):
    u = User(
        email="analyst@test.local",
        hashed_password=AuthService.hash_password("x"),
        role="analyst",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def viewer(db):
    u = User(
        email="viewer@test.local",
        hashed_password=AuthService.hash_password("x"),
        role="viewer",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def seeded_connections(db):
    src = DBConnection(name="PipeSrc", type="sqlite", config={"path": "/tmp/pipesrc.db"})
    tgt = DBConnection(name="PipeTgt", type="sqlite", config={"path": "/tmp/pipetgt.db"})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)
    return src, tgt


@pytest.fixture()
def seeded_published_mapping(db, seeded_connections):
    """A Mapping with a published MappingVersion whose schema_snapshot is
    populated — create_pipeline pins the current_version_id."""
    src, tgt = seeded_connections
    m = Mapping(
        name="Pipe Map",
        source_id=src.id,
        target_id=tgt.id,
        status="published",
        created_by="test",
    )
    db.add(m)
    db.flush()
    v = MappingVersion(
        mapping_id=m.id,
        version_number=1,
        status="published",
        published_by="test",
        schema_snapshot={
            "source": {"users": [{"name": "id", "type": "INTEGER"}]},
            "target": {"customers": [{"name": "id", "type": "INTEGER"}]},
        },
        edges_snapshot=[],
    )
    db.add(v)
    db.flush()
    m.current_version_id = v.id
    db.commit()
    db.refresh(m)
    return m, v


@pytest.fixture()
def physical_sqlite_connections(db, tmp_path):
    """Real SQLite files (not just recorded paths) with a source 'users'
    table (seeded with rows) and an empty target 'customers' table —
    needed by the execution engine tests, which actually move rows."""
    import sqlite3

    src_path = str(tmp_path / "pipe_exec_src.db")
    tgt_path = str(tmp_path / "pipe_exec_tgt.db")

    src_conn = sqlite3.connect(src_path)
    src_conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    src_conn.executemany(
        "INSERT INTO users (id, name, email) VALUES (?, ?, ?)",
        [(1, "Alice", "alice@x.com"), (2, "Bob", "bob@x.com"), (3, "Cara", "cara@x.com")],
    )
    src_conn.commit()
    src_conn.close()

    tgt_conn = sqlite3.connect(tgt_path)
    tgt_conn.execute("CREATE TABLE customers (cust_id INTEGER PRIMARY KEY, full_name TEXT, contact_email TEXT)")
    tgt_conn.commit()
    tgt_conn.close()

    src = DBConnection(name="ExecSrc", type="sqlite", config={"path": src_path})
    tgt = DBConnection(name="ExecTgt", type="sqlite", config={"path": tgt_path})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)
    return src, tgt


@pytest.fixture()
def seeded_mapping_with_field_mappings(db, physical_sqlite_connections):
    """A published Mapping + MappingVersion + real FieldMapping rows
    (version_id set, as MappingService.publish() does), mapping
    users.id/name/email -> customers.cust_id/full_name/contact_email
    with a direct transformation on each column."""
    from app.connectors.sqlite import SQLiteConnector
    from app.models.mapping import FieldMapping

    src, tgt = physical_sqlite_connections
    # Drive the schema_snapshot from the connector itself so it exactly
    # matches what validate_drift's live-schema fetch will compute —
    # hand-written column dicts would drift-mismatch on missing keys
    # (nullable/primary_key/foreign_keys) that SQLiteConnector always sets.
    src_connector = SQLiteConnector(src.config["path"])
    tgt_connector = SQLiteConnector(tgt.config["path"])
    source_schema = {t: src_connector.get_table_schema(t) for t in src_connector.get_tables()}
    target_schema = {t: tgt_connector.get_table_schema(t) for t in tgt_connector.get_tables()}
    src_connector.close()
    tgt_connector.close()

    m = Mapping(name="Exec Map", source_id=src.id, target_id=tgt.id,
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
