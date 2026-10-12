"""Fixtures for connector-credential vaulting tests (keeperdb_integration_tasks)."""
from __future__ import annotations

import base64
import os
import sys
import types

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
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

from app.api.routers.auth import get_current_user  # noqa: E402
from app.core import database as db_module  # noqa: E402
from app.core.audit_guard import install_audit_append_only_guard  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402

TEST_KEY = base64.b64encode(b"k" * 32).decode()
TEST_KEY_2 = base64.b64encode(b"m" * 32).decode()
SECRET_VALUE = "hunter2-super-secret-pw"  # the value every leak test greps for


@pytest.fixture(autouse=True)
def _aes_configured(monkeypatch):
    """Default every test to a configured aes256 backend; individual tests
    override for keeper/legacy scenarios."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "SECRET_MANAGER_BACKEND", "aes256")
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", TEST_KEY)
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY_PREVIOUS", None)
    # Fresh retrieve-audit batching window per test.
    from app.services import connection_secrets_service as svc
    svc._retrieve_audit_cache.clear()
    yield


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


class _NoCloseSession:
    def __init__(self, s):
        self._s = s

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._s, name)


@pytest.fixture()
def db(engine, monkeypatch):
    Session = sessionmaker(bind=engine)
    s = Session()
    # Backends/audit helpers that open their own SessionLocal share this
    # test's in-memory database.
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _NoCloseSession(s))
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


def _client_for(db, user):
    app.dependency_overrides[get_current_user] = lambda: user

    def _get_db_override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[db_module.get_db] = _get_db_override
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
