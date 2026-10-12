"""Per-owner isolation of the demo-data load/unload/status flow
(tenant_isolation_tasks slice #1). Reuses this directory's `db` fixture
(in-memory SQLite + driver stubs already installed by conftest.py)."""
from app.models.connection import DBConnection
from app.services.demo_data_service import (
    DEMO_CONNECTION_NAMES,
    is_demo_data_loaded,
    load_demo_data,
    unload_demo_data,
)


def test_load_creates_seven_rows_owned_by_the_caller(db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo_data_service.DEMO_DATA_DIR", str(tmp_path))
    result = load_demo_data(db, actor="alice@x", owner_email="alice@x")
    assert result == {"loaded": True, "connections_created": 7}
    rows = db.query(DBConnection).filter(DBConnection.owner_email == "alice@x").all()
    assert {r.name for r in rows} == set(DEMO_CONNECTION_NAMES)
    assert is_demo_data_loaded(db, owner_email="alice@x") is True


def test_two_owners_get_independent_copies(db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo_data_service.DEMO_DATA_DIR", str(tmp_path))
    load_demo_data(db, actor="alice@x", owner_email="alice@x")

    assert is_demo_data_loaded(db, owner_email="bob@x") is False
    result = load_demo_data(db, actor="bob@x", owner_email="bob@x")
    assert result == {"loaded": True, "connections_created": 7}
    assert is_demo_data_loaded(db, owner_email="alice@x") is True
    assert is_demo_data_loaded(db, owner_email="bob@x") is True

    total = db.query(DBConnection).filter(DBConnection.is_deleted == False).count()  # noqa: E712
    assert total == 14


def test_unload_only_affects_the_caller(db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo_data_service.DEMO_DATA_DIR", str(tmp_path))
    load_demo_data(db, actor="alice@x", owner_email="alice@x")
    load_demo_data(db, actor="bob@x", owner_email="bob@x")

    result = unload_demo_data(db, actor="alice@x", owner_email="alice@x")
    assert result == {"loaded": False, "connections_removed": 7}
    assert is_demo_data_loaded(db, owner_email="alice@x") is False
    assert is_demo_data_loaded(db, owner_email="bob@x") is True


def test_reload_after_unload_recreates_active_rows(db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo_data_service.DEMO_DATA_DIR", str(tmp_path))
    load_demo_data(db, actor="alice@x", owner_email="alice@x")
    unload_demo_data(db, actor="alice@x", owner_email="alice@x")
    assert is_demo_data_loaded(db, owner_email="alice@x") is False

    result = load_demo_data(db, actor="alice@x", owner_email="alice@x")
    assert result == {"loaded": True, "connections_created": 7}
    assert is_demo_data_loaded(db, owner_email="alice@x") is True
