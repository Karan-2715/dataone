"""Pytest fixtures for Schema Comparison Mode tests (Enterprise v2, E12).

Mirrors tests/schema/conftest.py's real-SQLite-file pattern — the
comparison endpoint does a live schema round-trip, not a DB-row lookup.
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
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.routers.auth import get_current_user  # noqa: E402
from app.core import database as db_module  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models.connection import DBConnection  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


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


def _make_user(db, email, role):
    u = User(email=email, hashed_password=AuthService.hash_password("x"), role=role, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def admin(db):
    return _make_user(db, "admin@test.local", "admin")


@pytest.fixture()
def viewer(db):
    return _make_user(db, "viewer@test.local", "viewer")


@pytest.fixture()
def make_client(db):
    def _make(user=None):
        def _get_db_override():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[db_module.get_db] = _get_db_override
        if user is not None:
            app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    try:
        yield _make
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client(make_client, admin):
    return make_client(admin)


@pytest.fixture()
def comparison_connections(db, tmp_path):
    """Real SQLite files exercising every diff category in one scenario:
    - 'users' is matched in both, with an added column (target-only),
      a missing column (source-only), a type mismatch, and a nullable +
      primary-key constraint change.
    - 'leads' exists only in source.
    - 'accounts' exists only in target."""
    import sqlite3

    src_path = str(tmp_path / "cmp_src.db")
    tgt_path = str(tmp_path / "cmp_tgt.db")

    src_conn = sqlite3.connect(src_path)
    src_conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, "
        "legacy_flag TEXT, age INTEGER NOT NULL)"
    )
    src_conn.execute("CREATE TABLE leads (id INTEGER PRIMARY KEY, company TEXT)")
    src_conn.commit()
    src_conn.close()

    tgt_conn = sqlite3.connect(tgt_path)
    tgt_conn.execute(
        "CREATE TABLE users (id INTEGER, name TEXT, "
        "email TEXT, age TEXT)"
    )
    tgt_conn.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, region TEXT)")
    tgt_conn.commit()
    tgt_conn.close()

    src = DBConnection(name="CmpSrc", type="sqlite", config={"path": src_path})
    tgt = DBConnection(name="CmpTgt", type="sqlite", config={"path": tgt_path})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)
    return src, tgt
