"""Pytest fixtures for Schema Intel tests (Task #6 drift completion, Task #1 catalog).

Provides:
- engine: in-memory SQLite engine with Base.metadata.create_all (so the
  new drift_event / catalog tables exist alongside existing tables).
- db: a Session bound to that engine.
- admin: a User fixture with role="admin" (Task #1 discovery tests, mirrors
  tests/mapping/conftest.py's fixture verbatim).
- seeded_connections: a (source, target) DBConnection pair (same as above).
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


# ── Optional DB driver stubs ─────────────────────────────────────
# Same pattern as tests/mapping/conftest.py and tests/pipelines/conftest.py.
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

    _add("psycopg2.extras", {
        "RealDictCursor": _Stub, "NamedTupleCursor": _Stub, "DictCursor": _Stub,
    })
    _add("pymysql.cursors", {
        "DictCursor": _Stub, "Cursor": _Stub, "SSDictCursor": _Stub,
    })
    _add("pymysql.connections", {
        "Connection": _Stub,
    })
    _add("oracledb.errors", {
        "DatabaseError": _Stub, "IntegrityError": _Stub, "OperationalError": _Stub,
    })


_install_driver_stubs()

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.database import Base  # noqa: E402
from app.models.connection import DBConnection  # noqa: E402
from app.models.schema_snapshot import SchemaSnapshot  # noqa: E402
from app.models.drift_event import DriftEvent  # noqa: E402
from app.models.schema_catalog import CatalogTable, CatalogColumn, CatalogForeignKey  # noqa: E402
from app.models.audit import AuditLog  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


@pytest.fixture()
def engine():
    # StaticPool + check_same_thread=False lets the in-memory SQLite engine be
    # shared safely across threads (needed by router-level TestClient tests)
    # — same pattern as tests/pipelines/conftest.py.
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
    src = DBConnection(name="SRC", type="sqlite", config={"path": "/tmp/src.db"})
    tgt = DBConnection(name="TGT", type="sqlite", config={"path": "/tmp/tgt.db"})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)
    return src, tgt


@pytest.fixture()
def physical_sqlite_connection(db, tmp_path):
    """A real SQLite file (not just a recorded path) with a 'people' table
    seeded with rows — needed by profiling tests, which actually query
    table data (null rate, distinct count, min/max, sample values)."""
    import sqlite3

    path = str(tmp_path / "profiling_source.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE people (id INTEGER PRIMARY KEY, contact TEXT, age INTEGER, notes TEXT)"
    )
    conn.executemany(
        "INSERT INTO people (id, contact, age, notes) VALUES (?, ?, ?, ?)",
        [
            (1, "alice@example.com", 30, "vip"),
            (2, "bob@example.com", 25, None),
            (3, "carol@example.com", 40, "vip"),
            (4, "not-an-email", 22, None),
            (5, "dave@example.com", 35, "vip"),
        ],
    )
    conn.commit()
    conn.close()

    db_conn = DBConnection(name="ProfilingSrc", type="sqlite", config={"path": path})
    db.add(db_conn)
    db.commit()
    db.refresh(db_conn)
    return db_conn