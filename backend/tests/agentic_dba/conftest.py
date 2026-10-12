"""Fixtures for Agentic DBA Copilot tests (agentic_dba_tasks #3-#9)."""
from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")
os.environ.setdefault("OLLAMA_MODEL", "llama3")
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

import sqlite3  # noqa: E402

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
from app.models.schema_catalog import CatalogColumn, CatalogTable, ColumnProfile  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


@pytest.fixture(autouse=True)
def _no_ollama_retries(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "OLLAMA_MAX_RETRIES", 0)
    # A live local Ollama would make plan proposals nondeterministic (the
    # LLM adaptation path really runs and may rename tables) — pin tests to
    # the deterministic template/catalog path; the LLM fallback contract has
    # its own dedicated test with a mocked HTTP boundary.
    monkeypatch.setattr(settings, "AGENTIC_DBA_LLM_ENABLED", False)


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


def _add_catalog(db, connection_id: int, schema: dict) -> dict:
    """schema: {table: [(col, type, nullable, pk), ...]} -> {(t, c): column_id}"""
    ids = {}
    for table_name, cols in schema.items():
        t = CatalogTable(connection_id=connection_id, table_name=table_name)
        db.add(t)
        db.flush()
        for pos, (col_name, dtype, nullable, pk) in enumerate(cols):
            c = CatalogColumn(table_id=t.id, column_name=col_name, data_type=dtype,
                              nullable=nullable, is_primary_key=pk, ordinal_position=pos)
            db.add(c)
            db.flush()
            ids[(table_name, col_name)] = c.id
    db.commit()
    return ids


@pytest.fixture()
def retail_connection(db, tmp_path):
    """A scanned + profiled retail-shaped SQLite source connection."""
    path = str(tmp_path / "retail.db")
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT);
        CREATE TABLE products (id INTEGER PRIMARY KEY, title TEXT, price REAL);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL, status TEXT);
        INSERT INTO customers VALUES (1,'Alice','a@x.com'),(2,'Bob','b@x.com'),(3,'Cara','c@x.com');
        INSERT INTO products VALUES (1,'Hat',9.5),(2,'Mug',4.0);
        INSERT INTO orders VALUES (1,1,20.0,'paid'),(2,1,5.0,'paid'),(3,2,9.5,'pending');
    """)
    raw.commit()
    raw.close()

    conn = DBConnection(name="retail-src", type="sqlite", config={"path": path})
    db.add(conn)
    db.commit()
    db.refresh(conn)

    ids = _add_catalog(db, conn.id, {
        "customers": [("id", "INTEGER", False, True), ("name", "TEXT", True, False),
                      ("email", "TEXT", True, False)],
        "products": [("id", "INTEGER", False, True), ("title", "TEXT", True, False),
                     ("price", "REAL", True, False)],
        "orders": [("id", "INTEGER", False, True), ("customer_id", "INTEGER", True, False),
                   ("total", "REAL", True, False), ("status", "TEXT", True, False)],
    })

    # Enriched profiles (agentic_dba_tasks #2 output shape) chosen so every
    # DQ rule type fires deterministically somewhere.
    profiles = [
        # customers.id: not_null + unique
        ((("customers", "id")), dict(null_count=0, null_rate=0.0, distinct_count=3,
                                     row_count=3, uniqueness_ratio=1.0, duplicate_count=0,
                                     sample_size_used=3)),
        # customers.email: near-unique with duplicates -> dedupe
        ((("customers", "email")), dict(null_count=0, null_rate=0.0, distinct_count=95,
                                        row_count=100, uniqueness_ratio=0.95, duplicate_count=2,
                                        sample_size_used=100)),
        # orders.customer_id: FK candidate to customers.id
        ((("orders", "customer_id")), dict(null_count=0, null_rate=0.0, distinct_count=2,
                                           row_count=3, uniqueness_ratio=0.67, duplicate_count=1,
                                           sample_size_used=3,
                                           fk_candidates=[{"table": "customers", "column": "id",
                                                           "overlap_ratio": 0.95}])),
    ]
    for (table, column), fields in profiles:
        db.add(ColumnProfile(column_id=ids[(table, column)], **fields))
    db.commit()
    return conn


@pytest.fixture()
def target_connection(db, tmp_path):
    """A distinct, empty target connection (for mapping auto-creation)."""
    path = str(tmp_path / "target.db")
    sqlite3.connect(path).close()
    conn = DBConnection(name="retail-target", type="sqlite", config={"path": path})
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
