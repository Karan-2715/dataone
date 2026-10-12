"""Pytest fixtures for the Dashboard test suite (dashboard_tasks #8).

Mirrors tests/pipelines/conftest.py: in-memory SQLite, driver stubs
installed before app imports, role-based user fixtures, plus a
TestClient factory with get_db / get_current_user overrides and an
autouse cache reset (the dashboard cache is a module-level singleton
that would otherwise leak summaries between tests).
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


# Stub optional DB drivers before any app import (same pattern as
# tests/mapping/conftest.py and tests/pipelines/conftest.py).
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

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.routers.auth import get_current_user  # noqa: E402
from app.core import database as db_module  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models.audit import AuditLog  # noqa: E402
from app.models.autopilot import AutopilotRun  # noqa: E402
from app.models.connection import DBConnection  # noqa: E402
from app.models.mapping import Mapping, MappingVersion  # noqa: E402
from app.models.pipeline import Pipeline, PipelineRun  # noqa: E402
from app.models.query_history import QueryHistory  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import dashboard_cache  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_dashboard_cache():
    """The cache is a module-level singleton keyed by (user_id, range);
    fresh in-memory DBs reuse the same user ids, so stale entries from a
    previous test would otherwise be served."""
    dashboard_cache.invalidate_all()
    yield
    dashboard_cache.invalidate_all()


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
def viewer(db):
    return _make_user(db, "viewer@test.local", "viewer")


@pytest.fixture()
def make_client(db):
    """Factory: TestClient authenticated as the given user (or anonymous
    when user is None). Not a context manager on purpose — the production
    lifespan seeds /shared/data on the host (see tests/mapping)."""
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
def seed_data(db):
    """A populated system: 2 connectors, 1 mapping (+1 soft-deleted),
    1 pipeline with a running and a failed run, 2 queries, audit events
    covering drift/security/connector/autopilot, 1 autopilot run, and one
    audit event 40 days old (outside every range)."""
    now = datetime.now(timezone.utc)

    src = DBConnection(name="Src", type="postgres", config={"host": "db"})
    tgt = DBConnection(name="Tgt", type="sqlite", config={"path": "/tmp/t.db"})
    db.add_all([src, tgt])
    db.flush()

    m = Mapping(name="M1", source_id=src.id, target_id=tgt.id,
                status="published", created_by="test")
    db.add(m)
    db.flush()
    v = MappingVersion(
        mapping_id=m.id, version_number=1, status="published",
        published_by="test",
        schema_snapshot={"source": {}, "target": {}}, edges_snapshot=[],
    )
    db.add(v)
    db.flush()
    m.current_version_id = v.id

    deleted = Mapping(name="Gone", source_id=src.id, target_id=tgt.id,
                      status="draft", created_by="test", deleted_at=now)
    db.add(deleted)

    p = Pipeline(name="P1", source_connection_id=src.id,
                 target_connection_id=tgt.id, mapping_id=m.id,
                 mapping_version_id=v.id, created_by="test")
    db.add(p)
    db.flush()
    db.add_all([
        PipelineRun(pipeline_id=p.id, status="running", trigger="manual",
                    started_at=now),
        PipelineRun(pipeline_id=p.id, status="failed", trigger="manual",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=1),
                    error_message="boom"),
        # Failed long ago — outside even the 30d range.
        PipelineRun(pipeline_id=p.id, status="failed", trigger="manual",
                    started_at=now - timedelta(days=40),
                    finished_at=now - timedelta(days=40)),
    ])

    db.add_all([
        QueryHistory(natural_query="q1", created_at=now - timedelta(hours=1)),
        QueryHistory(natural_query="q2", created_at=now - timedelta(days=2)),
    ])

    db.add_all([
        AuditLog(event_type="connector_created", actor="admin@test.local",
                 connection_name="Src", status="success",
                 created_at=now - timedelta(minutes=5)),
        AuditLog(event_type="schema_drift_detected", actor="system",
                 connection_name="Src", status="warning",
                 created_at=now - timedelta(hours=3)),
        AuditLog(event_type="security_alert", actor="system",
                 status="failure", created_at=now - timedelta(hours=4)),
        AuditLog(event_type="autopilot_run_completed", actor="system",
                 status="success", created_at=now - timedelta(hours=5)),
        AuditLog(event_type="mapping_created", actor="admin@test.local",
                 status="success", created_at=now - timedelta(days=40)),
    ])

    db.add(AutopilotRun(id="run-1", source_id=src.id, target_id=tgt.id,
                        mode="suggest", model="llama3", status="completed",
                        started_at=now - timedelta(hours=6)))
    db.commit()
    return {"source_id": src.id, "target_id": tgt.id,
            "mapping_id": m.id, "pipeline_id": p.id}
