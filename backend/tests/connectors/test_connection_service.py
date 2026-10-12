"""ConnectionService CRUD, soft-delete, health, dependents (tasks #1/#7)."""
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.audit import AuditLog
from app.models.connection import DBConnection
from app.services.connection_service import ConnectionService


def _create(db, name="conn-a", path="/tmp/a.db", owner_email="t@x"):
    return ConnectionService.create_connection(
        db, name=name, conn_type="sqlite", config={"path": path}, actor="t@x",
        owner_email=owner_email,
    )


# ── create ───────────────────────────────────────────────────────

def test_create_sets_defaults_and_actor(db):
    c = _create(db)
    assert c.health_status == "unknown"
    assert c.is_deleted is False
    assert c.created_by == "t@x"
    assert c.secrets_ref is None


def test_create_records_audit(db):
    c = _create(db)
    row = db.query(AuditLog).filter(AuditLog.event_type == "connector_created").one()
    assert row.connection_id == c.id
    assert row.actor == "t@x"


def test_create_rejects_bad_name(db):
    with pytest.raises(HTTPException) as e:
        ConnectionService.create_connection(
            db, name="bad name!", conn_type="sqlite",
            config={"path": "/tmp/x.db"}, actor="t@x", owner_email="t@x")
    assert e.value.status_code == 422


def test_create_duplicate_active_name_409(db):
    _create(db)
    with pytest.raises(HTTPException) as e:
        _create(db)
    assert e.value.status_code == 409


def test_partial_unique_index_allows_reusing_soft_deleted_name(db):
    c = _create(db)
    ConnectionService.soft_delete_connection(
        db, c.id, actor="t@x",
        dependents={"mappings": [], "pipelines": [], "total": 0})
    # same name can be created again now that the old row is soft-deleted
    c2 = _create(db)
    assert c2.id != c.id


def test_duplicate_active_name_blocked_at_db_level_too(db):
    # bypass the service (direct inserts) — the partial unique index must
    # still reject two active rows with the same (owner_email, name)
    db.add(DBConnection(name="dup", type="sqlite", config={"path": "/tmp/1.db"},
                        owner_email="t@x"))
    db.commit()
    db.add(DBConnection(name="dup", type="sqlite", config={"path": "/tmp/2.db"},
                        owner_email="t@x"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_different_owners_can_reuse_the_same_name(db):
    # per-user isolation (tenant_isolation_tasks slice #1): the same name is
    # only unique WITHIN one owner, not globally
    a = _create(db, "shared-name", "/tmp/a.db", owner_email="alice@x")
    b = _create(db, "shared-name", "/tmp/b.db", owner_email="bob@x")
    assert a.id != b.id
    assert a.owner_email == "alice@x"
    assert b.owner_email == "bob@x"


# ── read paths respect soft-delete ───────────────────────────────

def test_get_soft_deleted_404(db):
    c = _create(db)
    ConnectionService.soft_delete_connection(
        db, c.id, actor="t@x",
        dependents={"mappings": [], "pipelines": [], "total": 0})
    with pytest.raises(HTTPException) as e:
        ConnectionService.get_connection(db, c.id)
    assert e.value.status_code == 404


def test_get_connection_404_for_non_owner(db):
    # per-user isolation: another user's ID must 404, not leak via 403
    c = _create(db, owner_email="alice@x")
    with pytest.raises(HTTPException) as e:
        ConnectionService.get_connection(db, c.id, owner_email="bob@x")
    assert e.value.status_code == 404


def test_get_connection_owner_email_none_bypasses_scoping(db):
    # admin bypass: owner_email=None means unscoped
    c = _create(db, owner_email="alice@x")
    found = ConnectionService.get_connection(db, c.id, owner_email=None)
    assert found.id == c.id


def test_list_connections_scoped_by_owner(db):
    _create(db, "a", "/tmp/a.db", owner_email="alice@x")
    _create(db, "b", "/tmp/b.db", owner_email="bob@x")
    alice_names = [c.name for c in ConnectionService.list_connections(db, owner_email="alice@x")]
    assert alice_names == ["a"]
    all_names = sorted(c.name for c in ConnectionService.list_connections(db, owner_email=None))
    assert all_names == ["a", "b"]


def test_list_excludes_soft_deleted_by_default(db):
    a = _create(db, "a", "/tmp/a.db")
    b = _create(db, "b", "/tmp/b.db")
    ConnectionService.soft_delete_connection(
        db, a.id, actor="t@x",
        dependents={"mappings": [], "pipelines": [], "total": 0})
    names = [c.name for c in ConnectionService.list_connections(db)]
    assert names == ["b"]
    deleted = [c.name for c in ConnectionService.list_deleted(db)]
    assert deleted == ["a"]
    assert b.id  # silence unused


# ── health ───────────────────────────────────────────────────────

def test_update_health_transitions(db):
    c = _create(db)
    ConnectionService.update_health(db, c.id, "down", "boom")
    db.commit()
    db.refresh(c)
    assert c.health_status == "down"
    assert c.last_test_error == "boom"
    assert c.last_tested_at is not None

    ConnectionService.update_health(db, c.id, "healthy", None)
    db.commit()
    db.refresh(c)
    assert c.health_status == "healthy"
    assert c.last_test_error is None


def test_health_summary_counts(db):
    a = _create(db, "a", "/tmp/a.db")
    _create(db, "b", "/tmp/b.db")
    ConnectionService.update_health(db, a.id, "down", "x")
    db.commit()
    summary = ConnectionService.health_summary(db)
    assert summary["total"] == 2
    assert summary["down"] == 1
    assert summary["unknown"] == 1
    assert summary["last_tested_at"] is not None


# ── dependents + delete flows (task #7) ──────────────────────────

@pytest.fixture()
def conn_with_dependents(db):
    """Connection referenced by one mapping (source) + one enabled pipeline."""
    from app.models.mapping import Mapping, MappingVersion
    from app.models.pipeline import Pipeline

    src = _create(db, "dep-src", "/tmp/src.db")
    tgt = _create(db, "dep-tgt", "/tmp/tgt.db")

    m = Mapping(name="M", source_id=src.id, target_id=tgt.id,
                status="published", created_by="t")
    db.add(m)
    db.flush()
    v = MappingVersion(mapping_id=m.id, version_number=1, status="published",
                       published_by="t",
                       schema_snapshot={"source": {}, "target": {}},
                       edges_snapshot=[])
    db.add(v)
    db.flush()
    m.current_version_id = v.id
    p = Pipeline(name="P", source_connection_id=src.id,
                 target_connection_id=tgt.id, mapping_id=m.id,
                 mapping_version_id=v.id, enabled=True, created_by="t")
    db.add(p)
    db.commit()
    return src, m, p


def test_get_dependents_finds_mappings_and_pipelines(db, conn_with_dependents):
    src, m, p = conn_with_dependents
    deps = ConnectionService.get_dependents(db, src.id)
    assert deps["total"] == 2
    assert deps["mappings"][0]["id"] == m.id
    assert deps["mappings"][0]["role"] == "source"
    assert deps["pipelines"][0]["id"] == p.id


def test_soft_delete_disables_dependent_pipelines(db, conn_with_dependents):
    src, _m, p = conn_with_dependents
    deps = ConnectionService.get_dependents(db, src.id)
    ConnectionService.soft_delete_connection(db, src.id, actor="t@x",
                                             dependents=deps)
    db.refresh(p)
    assert p.enabled is False
    audit = db.query(AuditLog).filter(
        AuditLog.event_type == "connector_deleted").one()
    assert audit.payload["dependents_count"] == 2
    assert audit.payload["disabled_pipelines"] == [p.id]


def test_restore_soft_deleted(db):
    c = _create(db)
    ConnectionService.soft_delete_connection(
        db, c.id, actor="t@x",
        dependents={"mappings": [], "pipelines": [], "total": 0})
    restored = ConnectionService.restore_connection(db, c.id, actor="t@x")
    assert restored.is_deleted is False
    assert restored.deleted_at is None


def test_restore_name_clash_409(db):
    c = _create(db, "clash", "/tmp/1.db")
    ConnectionService.soft_delete_connection(
        db, c.id, actor="t@x",
        dependents={"mappings": [], "pipelines": [], "total": 0})
    _create(db, "clash", "/tmp/2.db")  # reuse the name while c is deleted
    with pytest.raises(HTTPException) as e:
        ConnectionService.restore_connection(db, c.id, actor="t@x")
    assert e.value.status_code == 409


def test_restore_missing_404(db):
    with pytest.raises(HTTPException) as e:
        ConnectionService.restore_connection(db, 999, actor="t@x")
    assert e.value.status_code == 404


def test_hard_delete_blocked_with_dependents(db, conn_with_dependents):
    src, _m, _p = conn_with_dependents
    with pytest.raises(HTTPException) as e:
        ConnectionService.hard_delete_connection(db, src.id, actor="t@x")
    assert e.value.status_code == 409


def test_hard_delete_removes_row(db):
    c = _create(db)
    ConnectionService.hard_delete_connection(db, c.id, actor="t@x")
    assert db.query(DBConnection).filter(DBConnection.id == c.id).first() is None
