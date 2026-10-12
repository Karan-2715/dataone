"""Pytest fixtures for Audit Trail tests (AUDIT-T1 canonical schema + SDK).

Provides:
- engine: in-memory SQLite engine with Base.metadata.create_all.
- db: a Session bound to that engine.
- admin: a User fixture with role="admin".
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
# Same pattern as tests/mapping/conftest.py and tests/schema_catalog/conftest.py.
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

from app.core import database as db_module  # noqa: E402
from app.core.audit_guard import install_audit_append_only_guard  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models.audit import AuditLog  # noqa: E402
from app.models.connection import DBConnection  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


@pytest.fixture()
def engine():
    # StaticPool + check_same_thread=False: TestClient serves requests on a
    # worker thread, so the in-memory SQLite connection must be shareable
    # across threads (same setup as tests/connectors/conftest.py).
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


@pytest.fixture(autouse=True)
def _reset_audit_buffer_and_circuit():
    """audit_db_circuit and the in-process buffer are module-level singletons
    (app.core.audit_buffer) — reset them around every test so state from one
    test (an OPEN circuit, leftover buffered events) can't leak into the next.
    """
    from app.core.audit_buffer import audit_db_circuit, drain_buffer

    drain_buffer()
    audit_db_circuit.record_success()  # forces CLOSED, failures=0
    yield
    drain_buffer()
    audit_db_circuit.record_success()


class _NoCloseSession:
    """Proxy that hands out the test's `db` session but ignores close()
    (same pattern as tests/mapping/test_suggest_task.py). Needed by any code
    path that opens its own SessionLocal() rather than using Depends(get_db)
    — e.g. the export endpoint's dedicated streaming session (see
    _stream_export_rows's docstring for why it can't reuse the request-scoped
    session) — so it lands on the test's isolated in-memory engine instead
    of the app's real (unmigrated, in tests) global engine."""

    def __init__(self, s):
        self._s = s

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._s, name)


@pytest.fixture()
def client(db, monkeypatch):
    """TestClient wired to the shared in-memory `db` session (audit router
    has no auth gating yet — that's audit_trail_tasks #7, out of scope)."""
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _NoCloseSession(db))

    def _get_db_override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[db_module.get_db] = _get_db_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
