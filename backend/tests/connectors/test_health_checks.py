"""Health-check scheduler tasks (task #5, FR5).

Celery task logic is exercised directly (eager, no broker); the dispatch
fan-out is asserted by capturing .delay() calls.
"""
from sqlalchemy.orm import sessionmaker

from app.models.connection import DBConnection
from app.tasks import connector_tasks


def _bind_task_sessions(monkeypatch, engine):
    """Point the tasks' SessionLocal at the test engine."""
    import app.core.database as db_module
    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(bind=engine))


def test_dispatch_one_task_per_active_connection(db, engine, monkeypatch, sqlite_file):
    db.add_all([
        DBConnection(name="a", type="sqlite", config={"path": sqlite_file}),
        DBConnection(name="b", type="sqlite", config={"path": sqlite_file}),
        DBConnection(name="dead", type="sqlite", config={"path": sqlite_file},
                     is_deleted=True),
    ])
    db.commit()
    _bind_task_sessions(monkeypatch, engine)

    dispatched = []
    monkeypatch.setattr(connector_tasks.run_health_check_for_connection, "delay",
                        lambda cid: dispatched.append(cid))

    result = connector_tasks.run_all_health_checks.apply().get()
    assert result["dispatched"] == 2
    deleted_id = db.query(DBConnection).filter(DBConnection.name == "dead").one().id
    assert deleted_id not in dispatched


def test_health_check_marks_healthy(db, engine, monkeypatch, sqlite_conn):
    _bind_task_sessions(monkeypatch, engine)
    result = connector_tasks.run_health_check_for_connection.apply(
        args=[sqlite_conn.id]).get()
    assert result == {"status": "completed", "connection_id": sqlite_conn.id,
                      "success": True, "error_code": None}
    db.refresh(sqlite_conn)
    assert sqlite_conn.health_status == "healthy"
    assert sqlite_conn.last_tested_at is not None


def test_health_check_marks_down_on_unreachable(db, engine, monkeypatch):
    conn = DBConnection(name="gone", type="sqlite",
                        config={"path": "/nonexistent/zzz.db"})
    db.add(conn)
    db.commit()
    _bind_task_sessions(monkeypatch, engine)

    result = connector_tasks.run_health_check_for_connection.apply(
        args=[conn.id]).get()
    assert result["success"] is False
    db.refresh(conn)
    assert conn.health_status == "down"
    assert conn.last_test_error


def test_health_check_skips_deleted_connection(db, engine, monkeypatch, sqlite_file):
    conn = DBConnection(name="soft-gone", type="sqlite",
                        config={"path": sqlite_file}, is_deleted=True)
    db.add(conn)
    db.commit()
    _bind_task_sessions(monkeypatch, engine)

    result = connector_tasks.run_health_check_for_connection.apply(
        args=[conn.id]).get()
    assert result["status"] == "skipped"
    db.refresh(conn)
    assert conn.health_status == "unknown"  # untouched


def test_beat_schedule_registered():
    from app.core.celery_app import celery_app
    beat = celery_app.conf.beat_schedule
    assert "health-check-all-connections" in beat
    assert beat["health-check-all-connections"]["task"] == \
        "app.tasks.connector_tasks.run_all_health_checks"
