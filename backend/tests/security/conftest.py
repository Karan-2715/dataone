"""Pytest fixtures for the Security Admin (DP-SEC-001) test suite."""
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
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.rbac_service import (  # noqa: E402
    AuthzService, backfill_user_roles, seed_default_roles, seed_permission_catalog,
)


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


@pytest.fixture(autouse=True)
def _reset_authz_cache():
    """AuthzService's cache is a class-level dict — reset between tests so
    one test's cached permission set can't leak into the next."""
    AuthzService._cache.clear()
    AuthzService._version = 0
    yield
    AuthzService._cache.clear()
    AuthzService._version = 0


@pytest.fixture()
def seeded(db):
    """Seed the permission catalog + 3 default roles, matching what
    app.main's lifespan does on real boot."""
    seed_permission_catalog(db)
    seed_default_roles(db)
    return db


@pytest.fixture()
def admin(seeded):
    u = User(email="admin@test.local", hashed_password=AuthService.hash_password("x"),
             role="admin", is_active=True)
    seeded.add(u)
    seeded.commit()
    seeded.refresh(u)
    backfill_user_roles(seeded)
    return u


@pytest.fixture()
def analyst(seeded):
    u = User(email="analyst@test.local", hashed_password=AuthService.hash_password("x"),
             role="analyst", is_active=True)
    seeded.add(u)
    seeded.commit()
    seeded.refresh(u)
    backfill_user_roles(seeded)
    return u


@pytest.fixture()
def viewer(seeded):
    u = User(email="viewer@test.local", hashed_password=AuthService.hash_password("x"),
             role="viewer", is_active=True)
    seeded.add(u)
    seeded.commit()
    seeded.refresh(u)
    backfill_user_roles(seeded)
    return u


@pytest.fixture()
def sales_connection(db, tmp_path):
    """A real SQLite file with a 'sales' table, incl. a PII-shaped 'owner_email'
    column for masking-policy enforcement tests."""
    import sqlite3

    path = str(tmp_path / "sales.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sales (id INTEGER PRIMARY KEY, region TEXT, amount REAL, "
        "qty INTEGER, owner_email TEXT)"
    )
    conn.executemany(
        "INSERT INTO sales (region, amount, qty, owner_email) VALUES (?, ?, ?, ?)",
        [
            ("west", 100.0, 2, "alice@acme.com"), ("west", 150.0, 3, "bob@acme.com"),
            ("east", 200.0, 1, "carol@acme.com"), ("east", 50.0, 4, "dave@acme.com"),
            ("north", 300.0, 5, "eve@acme.com"),
        ],
    )
    conn.commit()
    conn.close()

    db_conn = DBConnection(name="SalesDB", type="sqlite", config={"path": path})
    db.add(db_conn)
    db.commit()
    db.refresh(db_conn)
    return db_conn
