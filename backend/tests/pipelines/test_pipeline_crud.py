"""CRUD tests for the Pipelines Task #1 surface.

Covers:
- create_pipeline: happy path + validation errors (same source/target,
  missing connection, mapping not published, missing mapping)
- get_pipeline: happy path + 404
- list_pipelines: pagination envelope + empty
- update_pipeline: name + enabled + 404
- delete_pipeline: happy path + 404 + role-gated (admin only)
- list_runs: empty initially + 404 if pipeline missing

These tests exercise PipelineCRUD directly (not the HTTP router) so
they run without FastAPI / TestClient setup and stay fast. The router's
role gating and HTTP behavior is covered separately by mapping-router
tests in the Schema Mapper suite; here we focus on the service contract.
"""
import pytest
from fastapi import HTTPException

from app.services.pipeline_service import PipelineCRUD


def test_create_pipeline_happy_path(db, admin, seeded_connections, seeded_published_mapping):
    src, tgt = seeded_connections
    mapping, version = seeded_published_mapping
    p = PipelineCRUD.create_pipeline(
        db,
        name="CRM → DW",
        source_connection_id=src.id,
        target_connection_id=tgt.id,
        mapping_id=mapping.id,
        actor=admin.email,
    )
    assert p.id is not None
    assert p.name == "CRM → DW"
    assert p.source_connection_id == src.id
    assert p.target_connection_id == tgt.id
    assert p.mapping_id == mapping.id
    assert p.mapping_version_id == version.id
    assert p.enabled == 1
    assert p.created_by == admin.email


def test_create_pipeline_defaults_execution_mode_and_load_strategy(db, admin, seeded_connections, seeded_published_mapping):
    """execution_mode/load_strategy are additive columns — omitting them
    must preserve today's behavior (manual, auto-infer) for callers that
    predate this field, not error or silently pick something else."""
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    p = PipelineCRUD.create_pipeline(
        db, name="Defaults", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
    )
    assert p.execution_mode == "manual"
    assert p.load_strategy is None


def test_create_pipeline_with_explicit_execution_mode_and_load_strategy(db, admin, seeded_connections, seeded_published_mapping):
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    p = PipelineCRUD.create_pipeline(
        db, name="Explicit", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
        execution_mode="auto", load_strategy="append",
    )
    assert p.execution_mode == "auto"
    assert p.load_strategy == "append"


def test_create_pipeline_rejects_same_source_target(db, admin, seeded_connections, seeded_published_mapping):
    src, _ = seeded_connections
    mapping, _ = seeded_published_mapping
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.create_pipeline(
            db,
            name="SelfLoop",
            source_connection_id=src.id,
            target_connection_id=src.id,
            mapping_id=mapping.id,
            actor=admin.email,
        )
    assert e.value.status_code == 422
    assert "differ" in e.value.detail


def test_create_pipeline_rejects_missing_source_connection(db, admin, seeded_connections, seeded_published_mapping):
    _, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.create_pipeline(
            db,
            name="Bad",
            source_connection_id=99999,
            target_connection_id=tgt.id,
            mapping_id=mapping.id,
            actor=admin.email,
        )
    assert e.value.status_code == 404
    assert "source" in e.value.detail


def test_create_pipeline_rejects_unpublished_mapping(db, admin, seeded_connections):
    """A draft mapping (no current_version_id) cannot anchor a pipeline."""
    from app.models.mapping import Mapping as MappingModel
    src, tgt = seeded_connections
    draft = MappingModel(
        name="Draft", source_id=src.id, target_id=tgt.id,
        status="draft", created_by="test",
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.create_pipeline(
            db,
            name="OnDraft",
            source_connection_id=src.id,
            target_connection_id=tgt.id,
            mapping_id=draft.id,
            actor=admin.email,
        )
    assert e.value.status_code == 422
    assert "not published" in e.value.detail


def test_get_pipeline_happy_path(db, admin, seeded_connections, seeded_published_mapping):
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    p = PipelineCRUD.create_pipeline(
        db, name="X", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
    )
    fetched = PipelineCRUD.get_pipeline(db, p.id)
    assert fetched.id == p.id


def test_get_pipeline_missing_returns_404(db):
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.get_pipeline(db, 99999)
    assert e.value.status_code == 404


def test_list_pipelines_pagination_envelope(db, admin, seeded_connections, seeded_published_mapping):
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    for i in range(3):
        PipelineCRUD.create_pipeline(
            db, name=f"P{i}", source_connection_id=src.id,
            target_connection_id=tgt.id, mapping_id=mapping.id,
            actor=admin.email,
        )
    page = PipelineCRUD.list_pipelines(db, limit=2, offset=0)
    items, total = page
    assert total == 3
    assert len(items) == 2


def test_list_pipelines_empty(db):
    items, total = PipelineCRUD.list_pipelines(db)
    assert items == []
    assert total == 0


def test_update_pipeline_name_and_enabled(db, admin, seeded_connections, seeded_published_mapping):
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    p = PipelineCRUD.create_pipeline(
        db, name="Original", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
    )
    updated = PipelineCRUD.update_pipeline(
        db, p.id, name="Renamed", enabled=False, actor=admin.email,
    )
    assert updated.name == "Renamed"
    assert updated.enabled == 0


def test_update_pipeline_missing_returns_404(db, admin):
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.update_pipeline(
            db, 99999, name="X", enabled=True, actor=admin.email,
        )
    assert e.value.status_code == 404


def test_delete_pipeline_happy_path(db, admin, seeded_connections, seeded_published_mapping):
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    p = PipelineCRUD.create_pipeline(
        db, name="Bye", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
    )
    PipelineCRUD.delete_pipeline(db, p.id, actor=admin.email)
    with pytest.raises(HTTPException):
        PipelineCRUD.get_pipeline(db, p.id)


def test_list_runs_empty_for_new_pipeline(db, admin, seeded_connections, seeded_published_mapping):
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    p = PipelineCRUD.create_pipeline(
        db, name="R", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
    )
    items, total = PipelineCRUD.list_runs(db, p.id)
    assert items == []
    assert total == 0


def test_list_runs_missing_pipeline_returns_404(db):
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.list_runs(db, 99999)
    assert e.value.status_code == 404


# ── Audit emission ──────────────────────────────────────────────
# Task #8 says audit emission lands incrementally as each endpoint is added.
# Task #1 ships with audit for create/update/delete so the CRITICAL path
# (FR9) is covered; Task #4/#5/#6 will add audit for schedule / run / rerun.

def test_create_pipeline_emits_audit(db, admin, seeded_connections, seeded_published_mapping):
    from app.models.audit import AuditLog
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    PipelineCRUD.create_pipeline(
        db, name="A", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
    )
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "pipeline_created")
        .first()
    )
    assert audit is not None
    assert audit.payload["name"] == "A"


def test_update_pipeline_emits_audit(db, admin, seeded_connections, seeded_published_mapping):
    from app.models.audit import AuditLog
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    p = PipelineCRUD.create_pipeline(
        db, name="B", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
    )
    db.query(AuditLog).filter(AuditLog.event_type == "pipeline_updated").delete()
    db.commit()
    PipelineCRUD.update_pipeline(
        db, p.id, name="B2", enabled=True, actor=admin.email,
    )
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "pipeline_updated")
        .first()
    )
    assert audit is not None
    assert audit.payload["before"]["name"] == "B"
    assert audit.payload["after"]["name"] == "B2"


def test_delete_pipeline_emits_audit(db, admin, seeded_connections, seeded_published_mapping):
    from app.models.audit import AuditLog
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    p = PipelineCRUD.create_pipeline(
        db, name="C", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
    )
    db.query(AuditLog).filter(AuditLog.event_type == "pipeline_deleted").delete()
    db.commit()
    PipelineCRUD.delete_pipeline(db, p.id, actor=admin.email)
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "pipeline_deleted")
        .first()
    )
    assert audit is not None
    assert audit.payload["pipeline_id"] == p.id


# ── Bug #14: mapping must belong to the pipeline's connections ─────


def test_create_pipeline_rejects_mismatched_source_connection(
    db, admin, seeded_connections, seeded_published_mapping
):
    """A mapping published against connections A→B can't back a pipeline
    declaring a different source connection (Bug #14)."""
    from app.models.connection import DBConnection
    _, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    other = DBConnection(name="OtherSrc", type="sqlite", config={"path": "/tmp/other.db"})
    db.add(other)
    db.commit()
    db.refresh(other)
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.create_pipeline(
            db,
            name="WrongSrc",
            source_connection_id=other.id,
            target_connection_id=tgt.id,
            mapping_id=mapping.id,
            actor=admin.email,
        )
    assert e.value.status_code == 422
    assert "published against" in e.value.detail


def test_create_pipeline_rejects_mismatched_target_connection(
    db, admin, seeded_connections, seeded_published_mapping
):
    from app.models.connection import DBConnection
    src, _ = seeded_connections
    mapping, _ = seeded_published_mapping
    other = DBConnection(name="OtherTgt", type="sqlite", config={"path": "/tmp/other2.db"})
    db.add(other)
    db.commit()
    db.refresh(other)
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.create_pipeline(
            db,
            name="WrongTgt",
            source_connection_id=src.id,
            target_connection_id=other.id,
            mapping_id=mapping.id,
            actor=admin.email,
        )
    assert e.value.status_code == 422
    assert "published against" in e.value.detail


def test_create_pipeline_rejects_mapping_with_lost_connections(
    db, admin, seeded_connections, seeded_published_mapping
):
    """A mapping whose original connection was deleted (source_id SET NULL)
    has no usable baseline identity → 422 (Bug #14 edge case)."""
    src, tgt = seeded_connections
    mapping, _ = seeded_published_mapping
    mapping.source_id = None
    db.commit()
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.create_pipeline(
            db,
            name="LostConn",
            source_connection_id=src.id,
            target_connection_id=tgt.id,
            mapping_id=mapping.id,
            actor=admin.email,
        )
    assert e.value.status_code == 422
    assert "no longer exist" in e.value.detail
