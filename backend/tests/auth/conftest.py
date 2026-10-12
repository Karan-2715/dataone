"""Pytest fixtures for the Microsoft Entra SSO login tests (stage stopgap).

Mirrors tests/autopilot/conftest.py: in-memory SQLite engine, driver stubs
installed before app imports, a TestClient wired to the real app with only
get_db overridden (the new /entra/* routes are unauthenticated by design).
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

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core import database as db_module  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402

TEST_TENANT_ID = "test-tenant-id"


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
def existing_user(db):
    u = User(
        email="alice@veltris.com",
        hashed_password=AuthService.hash_password("x"),
        role="analyst",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def client(db):
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
def entra_configured(monkeypatch):
    """Configure Entra settings the same way the stage .env does."""
    monkeypatch.setattr(settings, "ENTRA_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "ENTRA_TENANT_ID", TEST_TENANT_ID)
    monkeypatch.setattr(settings, "ENTRA_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "ENTRA_REDIRECT_URI", "https://stage.example.com/api/v1/auth/entra/callback")
    monkeypatch.setattr(settings, "FRONTEND_LOGIN_URL", "https://stage.example.com/login")


class FakeMsalClient:
    """Stands in for msal.ConfidentialClientApplication — no network calls."""

    def __init__(self, claims=None, error=None):
        self._claims = claims
        self._error = error

    def get_authorization_request_url(self, scopes, state, redirect_uri):
        return f"https://login.microsoftonline.com/fake/authorize?state={state}"

    def acquire_token_by_authorization_code(self, code, scopes, redirect_uri):
        if self._error:
            return {"error": self._error}
        return {"access_token": "fake", "id_token_claims": self._claims}


@pytest.fixture()
def entra_auto_provision_domain(monkeypatch):
    """Temporary stopgap flag — see ENTRA_AUTO_PROVISION_DOMAIN in config.py."""
    monkeypatch.setattr(settings, "ENTRA_AUTO_PROVISION_DOMAIN", "veltris.com")


@pytest.fixture()
def patch_entra_client(monkeypatch):
    """Returns a function to install a FakeMsalClient for the router."""
    from app.api.routers import auth as auth_router

    def _patch(claims=None, error=None):
        fake = FakeMsalClient(claims=claims, error=error)
        monkeypatch.setattr(auth_router, "_entra_client", lambda: fake)
        return fake

    return _patch
