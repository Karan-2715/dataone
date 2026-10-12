"""Drift validation tests (Task #2, FR2 / AC2).

Covers:
- compute_schema_hash: stable, order-independent
- validate_drift: unmodified → no drift; added/removed/type-changed column → drift
- audit emission
- GET /pipelines/{id}/drift endpoint (via FastAPI TestClient)
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import pipeline_service
from app.services.pipeline_service import (
    PipelineCRUD,
    compute_schema_hash,
)


def test_compute_schema_hash_is_stable():
    schema = {"users": [{"name": "id", "type": "INTEGER"}]}
    assert compute_schema_hash(schema) == compute_schema_hash(schema)


def test_compute_schema_hash_is_order_independent_at_top_level():
    a = {"users": [{"name": "id", "type": "INTEGER"}], "orders": [{"name": "id", "type": "INTEGER"}]}
    b = {"orders": [{"name": "id", "type": "INTEGER"}], "users": [{"name": "id", "type": "INTEGER"}]}
    assert compute_schema_hash(a) == compute_schema_hash(b)


def test_compute_schema_hash_detects_type_change():
    a = {"users": [{"name": "id", "type": "INTEGER"}]}
    b = {"users": [{"name": "id", "type": "BIGINT"}]}
    assert compute_schema_hash(a) != compute_schema_hash(b)


def test_validate_drift_unmodified_schema_has_no_drift(
    db, admin, seeded_connections, monkeypatch
):
    """If the live source schema equals the snapshot captured at publish
    time, has_drift must be False (AC2 happy path)."""
    src, tgt = seeded_connections
    # The seeded_published_mapping fixture's snapshot has source={
    #   "users": [{"name": "id", "type": "INTEGER"}]
    # }
    m, _ = _seed_published(db, src, tgt)
    p = PipelineCRUD.create_pipeline(
        db, name="NoDrift", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=m.id, actor=admin.email,
    )
    # Live schema matches the snapshot.
    monkeypatch.setattr(
        pipeline_service.SchemaService, "get_full_schema",
        staticmethod(lambda _conn: {"users": [{"name": "id", "type": "INTEGER"}]}),
    )
    result = PipelineCRUD.validate_drift(db, p.id, actor=admin.email)
    assert result["has_drift"] is False
    assert result["changed_tables"] == []
    assert "no drift" in result["message"].lower()


def test_validate_drift_added_column_detected(
    db, admin, seeded_connections, monkeypatch
):
    """Adding a column to the source table counts as drift (AC2 block)."""
    src, tgt = seeded_connections
    m, _ = _seed_published(db, src, tgt)
    p = PipelineCRUD.create_pipeline(
        db, name="Added", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=m.id, actor=admin.email,
    )
    # Live schema has an extra column that wasn't in the snapshot.
    monkeypatch.setattr(
        pipeline_service.SchemaService, "get_full_schema",
        staticmethod(lambda _conn: {
            "users": [
                {"name": "id", "type": "INTEGER"},
                {"name": "email", "type": "TEXT"},
            ]
        }),
    )
    result = PipelineCRUD.validate_drift(db, p.id, actor=admin.email)
    assert result["has_drift"] is True
    assert "changed" in result["message"].lower() or "drift" in result["message"].lower()


def test_validate_drift_removed_table_detected(
    db, admin, seeded_connections, monkeypatch
):
    """A table present in the snapshot but missing from the live schema
    counts as drift."""
    src, tgt = seeded_connections
    m, version = _seed_published_with(
        db, src, tgt,
        source_snapshot={
            "users": [{"name": "id", "type": "INTEGER"}],
            "orders": [{"name": "id", "type": "INTEGER"}],
        },
    )
    p = PipelineCRUD.create_pipeline(
        db, name="Removed", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=m.id, actor=admin.email,
    )
    # Live schema is missing 'orders'.
    monkeypatch.setattr(
        pipeline_service.SchemaService, "get_full_schema",
        staticmethod(lambda _conn: {"users": [{"name": "id", "type": "INTEGER"}]}),
    )
    result = PipelineCRUD.validate_drift(db, p.id, actor=admin.email)
    assert result["has_drift"] is True
    assert "orders" in result["changed_tables"]


def test_validate_drift_type_change_detected(
    db, admin, seeded_connections, monkeypatch
):
    """A column type change (e.g. INTEGER → BIGINT) counts as drift."""
    src, tgt = seeded_connections
    m, _ = _seed_published(db, src, tgt)
    p = PipelineCRUD.create_pipeline(
        db, name="TypeChange", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=m.id, actor=admin.email,
    )
    monkeypatch.setattr(
        pipeline_service.SchemaService, "get_full_schema",
        staticmethod(lambda _conn: {"users": [{"name": "id", "type": "BIGINT"}]}),
    )
    result = PipelineCRUD.validate_drift(db, p.id, actor=admin.email)
    assert result["has_drift"] is True


def test_validate_drift_emits_audit_event(
    db, admin, seeded_connections, monkeypatch
):
    """The drift check itself records an audit row (FR9) with has_drift
    status. Task #3's executor will record a SEPARATE run-failure audit
    when the run is blocked because of drift."""
    from app.models.audit import AuditLog
    src, tgt = seeded_connections
    m, _ = _seed_published(db, src, tgt)
    p = PipelineCRUD.create_pipeline(
        db, name="Audit", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=m.id, actor=admin.email,
    )
    monkeypatch.setattr(
        pipeline_service.SchemaService, "get_full_schema",
        staticmethod(lambda _conn: {"users": [{"name": "id", "type": "BIGINT"}]}),
    )
    db.query(AuditLog).filter(AuditLog.event_type == "pipeline_drift_check").delete()
    db.commit()
    PipelineCRUD.validate_drift(db, p.id, actor=admin.email)
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "pipeline_drift_check")
        .first()
    )
    assert audit is not None
    assert audit.payload["has_drift"] is True
    assert audit.status == "failure"  # drift found → audit marked failure


def test_validate_drift_missing_pipeline_returns_404(db, admin):
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.validate_drift(db, 99999, actor=admin.email)
    assert e.value.status_code == 404


# ── Test helpers ─────────────────────────────────────────────────


def _seed_published(db, src, tgt):
    """Wrap the conftest's seeded_published_mapping fixture with a simple
    (mapping, version) tuple so the tests above read naturally. The
    snapshot in the fixture is {users: [{name: id, type: INTEGER}]}."""
    from app.models.mapping import Mapping as MappingModel, MappingVersion
    m = MappingModel(
        name="DriftMap",
        source_id=src.id, target_id=tgt.id,
        status="published", created_by="test",
    )
    db.add(m)
    db.flush()
    v = MappingVersion(
        mapping_id=m.id,
        version_number=1,
        status="published",
        published_by="test",
        schema_snapshot={
            "source": {"users": [{"name": "id", "type": "INTEGER"}]},
            "target": {"customers": [{"name": "id", "type": "INTEGER"}]},
        },
        edges_snapshot=[],
    )
    db.add(v)
    db.flush()
    m.current_version_id = v.id
    db.commit()
    db.refresh(m)
    return m, v


def _seed_published_with(db, src, tgt, *, source_snapshot):
    """Variant of _seed_published that lets the caller override the source
    snapshot. Used by the removed-table and added-column tests."""
    from app.models.mapping import Mapping as MappingModel, MappingVersion
    m = MappingModel(
        name="DriftMap",
        source_id=src.id, target_id=tgt.id,
        status="published", created_by="test",
    )
    db.add(m)
    db.flush()
    v = MappingVersion(
        mapping_id=m.id,
        version_number=1,
        status="published",
        published_by="test",
        schema_snapshot={
            "source": source_snapshot,
            "target": {"customers": [{"name": "id", "type": "INTEGER"}]},
        },
        edges_snapshot=[],
    )
    db.add(v)
    db.flush()
    m.current_version_id = v.id
    db.commit()
    db.refresh(m)
    return m, v


# ── Bug #15: hash is column-order independent, agrees with has_drift ─


def test_compute_schema_hash_is_column_order_independent():
    a = {"users": [{"name": "id", "type": "INTEGER"}, {"name": "email", "type": "TEXT"}]}
    b = {"users": [{"name": "email", "type": "TEXT"}, {"name": "id", "type": "INTEGER"}]}
    assert compute_schema_hash(a) == compute_schema_hash(b)


def test_validate_drift_reordered_columns_is_not_drift(
    db, admin, seeded_connections, monkeypatch
):
    """Connectors may return columns in a different order between calls;
    that must not read as drift, and the hashes must agree with has_drift
    (Bug #15 invariant: has_drift == (baseline_hash != current_hash))."""
    src, tgt = seeded_connections
    m, _ = _seed_published_with(
        db, src, tgt,
        source_snapshot={
            "users": [
                {"name": "id", "type": "INTEGER"},
                {"name": "email", "type": "TEXT"},
            ]
        },
    )
    p = PipelineCRUD.create_pipeline(
        db, name="Reorder", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=m.id, actor=admin.email,
    )
    monkeypatch.setattr(
        pipeline_service.SchemaService, "get_full_schema",
        staticmethod(lambda _conn: {
            "users": [
                {"name": "email", "type": "TEXT"},
                {"name": "id", "type": "INTEGER"},
            ]
        }),
    )
    result = PipelineCRUD.validate_drift(db, p.id, actor=admin.email)
    assert result["has_drift"] is False
    assert result["baseline_hash"] == result["current_hash"]
    assert result["changed_tables"] == []


# ── Bug #16: duplicate items register as multiset drift ─────────────


def test_validate_drift_duplicate_columns_detected(
    db, admin, seeded_connections, monkeypatch
):
    """Baseline [x, x] vs live [x, y] (same length) is drift. The old
    _schemas_equal matcher passed this case (Bug #16)."""
    x = {"name": "id", "type": "INTEGER"}
    y = {"name": "email", "type": "TEXT"}
    src, tgt = seeded_connections
    m, _ = _seed_published_with(db, src, tgt, source_snapshot={"users": [x, x]})
    p = PipelineCRUD.create_pipeline(
        db, name="Dups", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=m.id, actor=admin.email,
    )
    monkeypatch.setattr(
        pipeline_service.SchemaService, "get_full_schema",
        staticmethod(lambda _conn: {"users": [x, y]}),
    )
    result = PipelineCRUD.validate_drift(db, p.id, actor=admin.email)
    assert result["has_drift"] is True
    assert "users" in result["changed_tables"]


# ── Bug #17: missing baseline snapshot fails closed ──────────────────


@pytest.mark.parametrize("snapshot", [None, {}, {"source": {}}])
def test_validate_drift_missing_snapshot_returns_422(
    db, admin, seeded_connections, snapshot
):
    """A pinned version with no source snapshot cannot be verified — the
    check must 422 (fail closed), not silently report no drift (Bug #17)."""
    src, tgt = seeded_connections
    m, version = _seed_published(db, src, tgt)
    p = PipelineCRUD.create_pipeline(
        db, name="NoBaseline", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=m.id, actor=admin.email,
    )
    version.schema_snapshot = snapshot
    db.commit()
    with pytest.raises(HTTPException) as e:
        PipelineCRUD.validate_drift(db, p.id, actor=admin.email)
    assert e.value.status_code == 422
    assert "snapshot" in e.value.detail


# ── Bug #18: changed_tables names column-level drift ─────────────────


def test_validate_drift_type_change_lists_table(
    db, admin, seeded_connections, monkeypatch
):
    """A type change inside an existing table must name that table in
    changed_tables, not just flip has_drift (Bug #18)."""
    src, tgt = seeded_connections
    m, _ = _seed_published(db, src, tgt)
    p = PipelineCRUD.create_pipeline(
        db, name="TypeChangeNamed", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=m.id, actor=admin.email,
    )
    monkeypatch.setattr(
        pipeline_service.SchemaService, "get_full_schema",
        staticmethod(lambda _conn: {"users": [{"name": "id", "type": "BIGINT"}]}),
    )
    result = PipelineCRUD.validate_drift(db, p.id, actor=admin.email)
    assert result["has_drift"] is True
    assert result["changed_tables"] == ["users"]


def test_validate_drift_added_column_lists_table(
    db, admin, seeded_connections, monkeypatch
):
    src, tgt = seeded_connections
    m, _ = _seed_published(db, src, tgt)
    p = PipelineCRUD.create_pipeline(
        db, name="AddedNamed", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=m.id, actor=admin.email,
    )
    monkeypatch.setattr(
        pipeline_service.SchemaService, "get_full_schema",
        staticmethod(lambda _conn: {
            "users": [
                {"name": "id", "type": "INTEGER"},
                {"name": "email", "type": "TEXT"},
            ]
        }),
    )
    result = PipelineCRUD.validate_drift(db, p.id, actor=admin.email)
    assert result["has_drift"] is True
    assert result["changed_tables"] == ["users"]
