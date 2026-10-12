"""Fixtures for ACI integration tests (aci_integration_tasks)."""
from __future__ import annotations

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
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.audit_guard import install_audit_append_only_guard  # noqa: E402
from app.core.circuit_breaker import CircuitBreaker  # noqa: E402
from app.core.database import Base  # noqa: E402
import app.main  # noqa: E402, F401  (registers every model on Base)


@pytest.fixture(autouse=True)
def _fresh_aci_state(monkeypatch):
    """Each test gets a clean breaker + a configured-by-default integration
    (individual tests override to exercise the unconfigured path)."""
    from app.core.config import settings
    from app.services import aci_client_service

    fresh = CircuitBreaker("aci-test", failure_threshold=3, reset_timeout=30.0)
    monkeypatch.setattr(aci_client_service, "aci_circuit", fresh)
    monkeypatch.setattr(settings, "ACI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ACI_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "ACI_SLACK_INTERNAL_CHANNEL", "#dataplane-internal")
    yield


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.services.aci_client_service.time.sleep", lambda s: None)


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


class FakeAciClient:
    """Stands in for the ACI SDK client at the _get_client boundary."""

    def __init__(self):
        self.executed: list = []
        self.fail_with: Exception | None = None

    class _Functions:
        def __init__(self, outer):
            self.outer = outer

        def search(self, intent=None, limit=None, **kwargs):
            if self.outer.fail_with:
                raise self.outer.fail_with
            return [{"name": "SLACK__CHAT_POST_MESSAGE", "description": "Post a message"}]

        def execute(self, function_name, function_arguments, linked_account_owner_id):
            if self.outer.fail_with:
                raise self.outer.fail_with
            self.outer.executed.append({
                "function_name": function_name,
                "function_arguments": function_arguments,
                "linked_account_owner_id": linked_account_owner_id,
            })
            return {"success": True, "data": {"ok": True}, "error": None}

    class _LinkedAccounts:
        def __init__(self, outer):
            self.outer = outer

        def list(self, **kwargs):
            if self.outer.fail_with:
                raise self.outer.fail_with
            return [{"id": "la-1", "app_name": "SLACK",
                     "linked_account_owner_id": "dataone", "enabled": True}]

    @property
    def functions(self):
        return self._Functions(self)

    @property
    def linked_accounts(self):
        return self._LinkedAccounts(self)


@pytest.fixture()
def fake_aci(monkeypatch):
    """Inject a FakeAciClient at the SDK boundary (never real network)."""
    from app.services.aci_client_service import AciClientService, aci_client

    fake = FakeAciClient()
    monkeypatch.setattr(AciClientService, "_get_client", lambda self: fake)
    aci_client._client = None
    return fake
