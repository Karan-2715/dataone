"""Pipeline notify-out tests (aci_integration_tasks #7): failure/success/
drift-impact independently configurable, run state never touched by
notification behavior."""
from __future__ import annotations

import pytest

from app.models.connection import DBConnection
from app.models.mapping import Mapping, MappingVersion
from app.models.pipeline import Pipeline, PipelineRun
from app.services.notification_service import set_notify_enabled
from app.services.pipeline_executor import _update_run_status


@pytest.fixture()
def dispatched(monkeypatch):
    calls: list = []

    class _FakeTask:
        @staticmethod
        def delay(**kwargs):
            calls.append(kwargs)

    import app.tasks.aci_tasks as aci_tasks
    monkeypatch.setattr(aci_tasks, "notify_out_task", _FakeTask)
    return calls


@pytest.fixture()
def run(db, tmp_path):
    import sqlite3
    path = str(tmp_path / "p.db")
    sqlite3.connect(path).close()
    src = DBConnection(name="p-src", type="sqlite", config={"path": path})
    tgt = DBConnection(name="p-tgt", type="sqlite", config={"path": path})
    db.add_all([src, tgt])
    db.flush()
    mapping = Mapping(name="m", source_id=src.id, target_id=tgt.id, status="published")
    db.add(mapping)
    db.flush()
    version = MappingVersion(mapping_id=mapping.id, version_number=1, status="published")
    db.add(version)
    db.flush()
    pipeline = Pipeline(name="p1", source_connection_id=src.id,
                        target_connection_id=tgt.id, mapping_id=mapping.id,
                        mapping_version_id=version.id)
    db.add(pipeline)
    db.flush()
    r = PipelineRun(pipeline_id=pipeline.id, status="running")
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_failed_run_with_failure_notify_enabled_dispatches(db, run, dispatched, monkeypatch):
    _patch_sessionlocal(db, monkeypatch)
    set_notify_enabled(db, "pipeline:run_failure", True, actor="admin@x")
    db.commit()
    _update_run_status(db, run.id, "failed", error="extract exploded")
    assert len(dispatched) == 1
    assert dispatched[0]["event_key"] == "pipeline:run_failure"
    assert "failed" in dispatched[0]["title"]
    assert "extract exploded" in dispatched[0]["body"]


def test_successful_run_with_only_failure_notify_does_not_dispatch(db, run, dispatched, monkeypatch):
    _patch_sessionlocal(db, monkeypatch)
    set_notify_enabled(db, "pipeline:run_failure", True, actor="admin@x")
    db.commit()
    _update_run_status(db, run.id, "succeeded", rows=42)
    assert dispatched == []


def test_success_notify_is_independently_configurable(db, run, dispatched, monkeypatch):
    _patch_sessionlocal(db, monkeypatch)
    set_notify_enabled(db, "pipeline:run_success", True, actor="admin@x")
    db.commit()
    _update_run_status(db, run.id, "succeeded", rows=42)
    assert len(dispatched) == 1
    assert dispatched[0]["event_key"] == "pipeline:run_success"
    assert "42 rows" in dispatched[0]["title"]


def test_drift_blocked_run_uses_drift_impact_event(db, run, dispatched, monkeypatch):
    _patch_sessionlocal(db, monkeypatch)
    set_notify_enabled(db, "pipeline:drift_impact", True, actor="admin@x")
    # run_failure NOT enabled — drift-impact is its own event key.
    db.commit()
    _update_run_status(db, run.id, "failed",
                       error="blocked by schema drift: columns changed (tables: customers)")
    assert len(dispatched) == 1
    assert dispatched[0]["event_key"] == "pipeline:drift_impact"
    assert "drift" in dispatched[0]["title"]


def test_run_state_persists_regardless_of_notification(db, run, dispatched, monkeypatch):
    _patch_sessionlocal(db, monkeypatch)

    class _ExplodingTask:
        @staticmethod
        def delay(**kwargs):
            raise RuntimeError("broker down")

    import app.tasks.aci_tasks as aci_tasks
    set_notify_enabled(db, "pipeline:run_failure", True, actor="admin@x")
    db.commit()
    monkeypatch.setattr(aci_tasks, "notify_out_task", _ExplodingTask)

    _update_run_status(db, run.id, "failed", error="boom")
    db.refresh(run)
    assert run.status == "failed"       # business state fully intact
    assert run.error_message == "boom"


def _patch_sessionlocal(db, monkeypatch):
    """_update_run_status commits on the passed session — nothing to patch,
    helper kept for symmetry/clarity in the tests above."""
    return db
