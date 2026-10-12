"""Shared pytest fixtures for mapping tests."""
from __future__ import annotations

import os
import sys
import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")
os.environ.setdefault("OLLAMA_MODEL", "llama3")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "True")
os.environ.setdefault("SECRET_KEY", "test-secret")


# ── Optional DB driver stubs ─────────────────────────────────────────────
# The mapping tests run against SQLite only. Some modules under app.connectors
# eagerly `import psycopg2` / `pymysql` / `oracledb` at module load time even
# when those backends are never used. In a slim test env we stub them out (and
# their sub-modules) so collection succeeds. Real production installs still
# get the real drivers.
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
        mod.__path__ = []  # mark as package
        sys.modules[name] = mod
        for sub in sub_modules.get(name, ()):
            full = f"{name}.{sub}"
            if full not in sys.modules:
                sys.modules[full] = types.ModuleType(full)

    # Stub the symbols our connectors import from driver sub-modules so
    # attribute access at import time doesn't blow up if anything ever reaches
    # them. Real production installs still get the real drivers.
    class _Stub:  # noqa: D401 - trivial placeholder class
        pass

    def _add(module_name: str, attrs: dict) -> None:
        mod = sys.modules.get(module_name)
        if mod is None:
            return
        for k, v in attrs.items():
            if not hasattr(mod, k):
                setattr(mod, k, v)

    _add("psycopg2.extras", {
        "RealDictCursor": _Stub,
        "NamedTupleCursor": _Stub,
        "DictCursor": _Stub,
    })
    _add("pymysql.cursors", {
        "DictCursor": _Stub,
        "Cursor": _Stub,
        "SSDictCursor": _Stub,
    })
    _add("pymysql.connections", {
        "Connection": _Stub,
    })
    _add("oracledb.errors", {
        "DatabaseError": _Stub,
        "IntegrityError": _Stub,
        "OperationalError": _Stub,
    })

_install_driver_stubs()

from app.core.database import Base  # noqa: E402
from app.models.connection import DBConnection  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


@pytest.fixture()
def engine():
    # StaticPool + check_same_thread=False lets the in-memory SQLite engine be
    # shared safely across threads (FastAPI's TestClient runs request handlers
    # on an asyncio thread pool, not the test thread). This is the canonical
    # SQLAlchemy pattern for multi-threaded in-memory SQLite.
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
def seeded_connections(db, tmp_path):
    import sqlite3

    src_path = str(tmp_path / "mapping_src.db")
    tgt_path = str(tmp_path / "mapping_tgt.db")
    with sqlite3.connect(src_path) as raw:
        raw.execute("CREATE TABLE s1 (c1 TEXT, c2 TEXT)")
        raw.execute("CREATE TABLE t1 (c1 TEXT, c2 TEXT)")
        raw.execute("CREATE TABLE t2 (c2 TEXT)")
    with sqlite3.connect(tgt_path) as raw:
        raw.execute("CREATE TABLE t1 (c1 TEXT, c2 INTEGER)")
        raw.execute("CREATE TABLE t2 (c2 TEXT)")
    src = DBConnection(name="SRC", type="sqlite", config={"path": src_path})
    tgt = DBConnection(name="TGT", type="sqlite", config={"path": tgt_path})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)
    return src, tgt
