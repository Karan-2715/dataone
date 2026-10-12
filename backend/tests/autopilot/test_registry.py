"""ai_autopilot_tasks #4: registry invariants + guardrail gate."""
import pytest

from app.services.autopilot_registry import (
    ACTION_REGISTRY,
    PROHIBITED_ACTION_TYPES,
    PayloadValidationError,
    ProhibitedActionError,
    UnknownActionError,
    check_action_allowed,
    validate_payload,
)


def test_auto_capable_implies_reversible_low_risk():
    for spec in ACTION_REGISTRY.values():
        if spec.auto_capable:
            assert spec.reversible, spec.action_type
            assert spec.risk == "low", spec.action_type


def test_migration_execute_is_never_auto_capable():
    spec = ACTION_REGISTRY["migration_execute"]
    assert spec.auto_capable is False
    assert spec.reversible is False
    assert spec.risk == "high"


@pytest.mark.parametrize("action_type", sorted(PROHIBITED_ACTION_TYPES))
def test_prohibited_types_raise(action_type):
    with pytest.raises(ProhibitedActionError) as e:
        check_action_allowed(action_type)
    assert "regardless of policy" in str(e.value)


def test_unknown_type_default_denied():
    with pytest.raises(UnknownActionError):
        check_action_allowed("format_all_disks")


def test_registered_types_pass():
    for action_type in ACTION_REGISTRY:
        assert check_action_allowed(action_type).action_type == action_type


def test_no_prohibited_type_is_registered():
    assert not PROHIBITED_ACTION_TYPES & set(ACTION_REGISTRY)


def test_validate_payload_missing_key():
    spec = ACTION_REGISTRY["connector_health_check"]
    with pytest.raises(PayloadValidationError):
        validate_payload(spec, {})


def test_validate_payload_coerces_ids():
    spec = ACTION_REGISTRY["connector_health_check"]
    assert validate_payload(spec, {"connection_id": "7"}) == {"connection_id": 7}
    with pytest.raises(PayloadValidationError):
        validate_payload(spec, {"connection_id": "not-a-number"})


# ── pipeline_schedule_disable (pipeline-failure-escalation design) ────────


def test_pipeline_schedule_disable_is_registered_and_never_auto_capable():
    spec = ACTION_REGISTRY["pipeline_schedule_disable"]
    assert spec.auto_capable is False
    assert spec.reversible is True
    assert spec.required_payload_keys == frozenset({"pipeline_id"})


def _make_scheduled_pipeline(db, two_conns, *, schedule_enabled=True):
    """Build a Pipeline + Schedule via the real CRUD paths, mirroring
    tests/pipelines/test_pipeline_crud.py's seeded_connections/seeded_published_mapping
    fixtures (not available in tests/autopilot/conftest.py, so built inline here)."""
    from app.models.mapping import Mapping, MappingVersion
    from app.services.pipeline_service import PipelineCRUD

    src, tgt = two_conns
    mapping = Mapping(
        name="Escalation Map", source_id=src.id, target_id=tgt.id,
        status="published", created_by="test",
    )
    db.add(mapping)
    db.flush()
    version = MappingVersion(
        mapping_id=mapping.id, version_number=1, status="published",
        published_by="test",
        schema_snapshot={"source": {}, "target": {}}, edges_snapshot=[],
    )
    db.add(version)
    db.flush()
    mapping.current_version_id = version.id
    db.commit()

    pipeline = PipelineCRUD.create_pipeline(
        db, name="Escalation Pipeline",
        source_connection_id=src.id, target_connection_id=tgt.id,
        mapping_id=mapping.id, actor="test",
    )
    PipelineCRUD.upsert_schedule(
        db, pipeline.id, cron_expression="0 * * * *",
        enabled=schedule_enabled, timezone="UTC", actor="test",
    )
    db.commit()
    db.refresh(pipeline)
    return pipeline


def test_exec_pipeline_schedule_disable_raises_without_schedule(db, two_conns):
    from app.models.mapping import Mapping, MappingVersion
    from app.services.autopilot_registry import _exec_pipeline_schedule_disable
    from app.services.pipeline_service import PipelineCRUD

    src, tgt = two_conns
    mapping = Mapping(
        name="No Schedule Map", source_id=src.id, target_id=tgt.id,
        status="published", created_by="test",
    )
    db.add(mapping)
    db.flush()
    version = MappingVersion(
        mapping_id=mapping.id, version_number=1, status="published",
        published_by="test",
        schema_snapshot={"source": {}, "target": {}}, edges_snapshot=[],
    )
    db.add(version)
    db.flush()
    mapping.current_version_id = version.id
    db.commit()

    pipeline = PipelineCRUD.create_pipeline(
        db, name="No Schedule Pipeline",
        source_connection_id=src.id, target_connection_id=tgt.id,
        mapping_id=mapping.id, actor="test",
    )
    db.commit()

    with pytest.raises(ValueError, match="no schedule"):
        _exec_pipeline_schedule_disable(db, {"pipeline_id": pipeline.id}, "test")


def test_exec_pipeline_schedule_disable_disables_existing_schedule(db, two_conns):
    from app.services.autopilot_registry import _exec_pipeline_schedule_disable

    pipeline = _make_scheduled_pipeline(db, two_conns, schedule_enabled=True)

    result = _exec_pipeline_schedule_disable(db, {"pipeline_id": pipeline.id}, "test")
    assert result == {"pipeline_id": pipeline.id, "schedule_disabled": True}
    db.refresh(pipeline.schedule)
    assert pipeline.schedule.enabled is False


def test_exec_pipeline_schedule_disable_raises_for_missing_pipeline(db):
    from app.services.autopilot_registry import _exec_pipeline_schedule_disable

    with pytest.raises(ValueError, match="not found"):
        _exec_pipeline_schedule_disable(db, {"pipeline_id": 999999}, "test")
