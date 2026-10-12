"""Pytest fixtures for AskData Bot tests (askdata_bot_tasks #1-#7)."""
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
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.routers.auth import get_current_user  # noqa: E402
from app.core import database as db_module  # noqa: E402
from app.core.audit_guard import install_audit_append_only_guard  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models.connection import DBConnection  # noqa: E402
from app.models.schema_catalog import CatalogColumn, CatalogTable  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


@pytest.fixture(autouse=True)
def _no_ollama_retries(monkeypatch):
    """Ollama isn't reachable in tests — skip its retry/backoff sleep so
    generation falls through to the deterministic heuristic fast instead of
    burning several seconds per test on connection-refused retries."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "OLLAMA_MAX_RETRIES", 0)


@pytest.fixture()
def engine():
    from sqlalchemy.pool import StaticPool
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    install_audit_append_only_guard(eng)
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
def analyst(db):
    return _make_user(db, "analyst@test.local", "analyst")


@pytest.fixture()
def viewer(db):
    return _make_user(db, "viewer@test.local", "viewer")


@pytest.fixture()
def sqlite_conn_scanned(db, tmp_path):
    """A connection with real data AND a persisted Schema Intel catalog
    (grounded generation path)."""
    import sqlite3
    path = str(tmp_path / "askdata_test.db")
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, active INTEGER)"
    )
    raw.execute(
        "INSERT INTO customers (name, email, active) VALUES "
        "('Alice', 'alice@x.com', 1), ('Bob', 'bob@x.com', 1), ('Carol', 'carol@x.com', 0)"
    )
    raw.commit()
    raw.close()

    conn = DBConnection(name="askdata-sqlite", type="sqlite", config={"path": path})
    db.add(conn)
    db.commit()
    db.refresh(conn)

    table = CatalogTable(connection_id=conn.id, table_name="customers")
    db.add(table)
    db.commit()
    db.refresh(table)
    db.add_all([
        CatalogColumn(table_id=table.id, column_name="id", data_type="INTEGER",
                      nullable=False, is_primary_key=True, ordinal_position=0),
        CatalogColumn(table_id=table.id, column_name="name", data_type="TEXT",
                      nullable=True, is_primary_key=False, ordinal_position=1),
        CatalogColumn(table_id=table.id, column_name="email", data_type="TEXT",
                      nullable=True, is_primary_key=False, ordinal_position=2),
        CatalogColumn(table_id=table.id, column_name="active", data_type="INTEGER",
                      nullable=True, is_primary_key=False, ordinal_position=3),
    ])
    db.commit()
    return conn


@pytest.fixture()
def sqlite_conn_unscanned(db, tmp_path):
    """A connection with real data but NO persisted catalog (fallback path)."""
    import sqlite3
    path = str(tmp_path / "askdata_unscanned.db")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)")
    raw.execute("INSERT INTO widgets (name) VALUES ('bolt'), ('nut')")
    raw.commit()
    raw.close()

    conn = DBConnection(name="askdata-unscanned", type="sqlite", config={"path": path})
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


class _NoCloseSession:
    def __init__(self, s):
        self._s = s

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._s, name)


def _client_for(db, user, monkeypatch):
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _NoCloseSession(db))
    app.dependency_overrides[get_current_user] = lambda: user

    def _get_db_override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[db_module.get_db] = _get_db_override
    return TestClient(app)


@pytest.fixture()
def client_admin(db, admin, monkeypatch):
    c = _client_for(db, admin, monkeypatch)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_analyst(db, analyst, monkeypatch):
    c = _client_for(db, analyst, monkeypatch)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_viewer(db, viewer, monkeypatch):
    c = _client_for(db, viewer, monkeypatch)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()
