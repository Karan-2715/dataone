"""Pytest fixtures for the Autopilot governance suite (ai_autopilot_tasks).

Mirrors tests/connectors/conftest.py: in-memory SQLite engine, driver
stubs installed before app imports, user fixtures, plus TestClient
fixtures wired to the real app with get_db/get_current_user overridden.
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
# Tests run against SQLite only; psycopg2/pymysql/oracledb are imported
# eagerly by their connectors even when unused. Same pattern as
# tests/pipelines/conftest.py and tests/mapping/conftest.py.
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

import sqlite3  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.routers.auth import get_current_user  # noqa: E402
from app.core import database as db_module  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models.connection import DBConnection  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


@pytest.fixture()
def engine():
    # StaticPool + check_same_thread=False: TestClient serves requests on a
    # worker thread, so the in-memory SQLite connection must be shareable
    # across threads (same setup as tests/mapping/conftest.py).
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


def _make_user(db, email, role):
    u = User(
        email=email,
        hashed_password=AuthService.hash_password("x"),
        role=role,
        is_active=True,
    )
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
def sqlite_file(tmp_path):
    """Real SQLite file with one table — connectivity tests hit it live."""
    path = str(tmp_path / "conn_test.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def sqlite_conn(db, sqlite_file):
    conn = DBConnection(name="test-sqlite", type="sqlite",
                        config={"path": sqlite_file})
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


def _client_for(db, user):
    app.dependency_overrides[get_current_user] = lambda: user

    def _get_db_override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[db_module.get_db] = _get_db_override
    # No `with TestClient(app)` — the production lifespan seeds /shared/data;
    # tests run against the in-memory engine only (same note as the mapping
    # router tests).
    return TestClient(app)


@pytest.fixture()
def client_admin(db, admin):
    c = _client_for(db, admin)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_analyst(db, analyst):
    c = _client_for(db, analyst)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def viewer(db):
    return _make_user(db, "viewer@test.local", "viewer")


@pytest.fixture()
def client_viewer(db, viewer):
    c = _client_for(db, viewer)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_unauth(db):
    """get_current_user NOT overridden — requests carry no token."""
    def _get_db_override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[db_module.get_db] = _get_db_override
    c = TestClient(app)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def two_conns(db, sqlite_file, tmp_path):
    """Two live sqlite connections (source/target) for run + executor tests."""
    import sqlite3 as _sqlite3
    path2 = str(tmp_path / "conn_test_2.db")
    c2 = _sqlite3.connect(path2)
    c2.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT)")
    c2.commit()
    c2.close()
    src = DBConnection(name="AP-SRC", type="sqlite", config={"path": sqlite_file})
    tgt = DBConnection(name="AP-TGT", type="sqlite", config={"path": path2})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)
    return src, tgt


@pytest.fixture()
def pending_health_rec(db, two_conns):
    """A pending connector_health_check recommendation for two_conns[0]."""
    from app.services.autopilot_service import AutopilotService
    src, _ = two_conns
    rec, created = AutopilotService.upsert_recommendation(
        db,
        action_type="connector_health_check",
        subject=f"connection:{src.id}",
        payload={"connection_id": src.id},
        rationale={"summary": "test rec", "evidence": [], "trigger": {}},
        confidence=90.0,
        created_by="autopilot-engine",
    )
    db.commit()
    assert created
    return rec
