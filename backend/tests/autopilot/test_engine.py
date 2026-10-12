"""ai_autopilot_tasks #5: trigger evaluators, dedupe, supersede."""
from app.models.audit import AuditLog
from app.models.autopilot import AutopilotRecommendation
from app.models.drift_event import DriftEvent
from app.models.mapping import Mapping
from app.services.autopilot_engine import AutopilotEngine


def _recs(db, action_type=None, status=None):
    q = db.query(AutopilotRecommendation)
    if action_type:
        q = q.filter(AutopilotRecommendation.action_type == action_type)
    if status:
        q = q.filter(AutopilotRecommendation.status == status)
    return q.all()


def test_down_connection_creates_health_rec(db, two_conns):
    src, _ = two_conns
    src.health_status = "down"
    src.last_test_error = "connection refused"
    db.commit()

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 1
    rec = _recs(db, "connector_health_check", "pending")[0]
    assert rec.payload == {"connection_id": src.id}
    assert rec.subject == f"connection:{src.id}"
    assert "down" in rec.rationale["summary"]
    assert any("connection refused" in e for e in rec.rationale["evidence"])
    assert rec.confidence == 90.0
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "autopilot_recommendation_created")
        .first()
    )
    assert audit is not None


def test_reevaluation_refreshes_not_duplicates(db, two_conns):
    src, _ = two_conns
    src.health_status = "degraded"
    db.commit()

    first = AutopilotEngine.evaluate_all(db)
    second = AutopilotEngine.evaluate_all(db)
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["refreshed"] == 1
    assert len(_recs(db, "connector_health_check")) == 1


def test_recovered_connection_supersedes_rec(db, two_conns):
    src, _ = two_conns
    src.health_status = "down"
    db.commit()
    AutopilotEngine.evaluate_all(db)

    src.health_status = "healthy"
    db.commit()
    counts = AutopilotEngine.evaluate_all(db)
    assert counts["superseded"] == 1
    rec = _recs(db, "connector_health_check")[0]
    assert rec.status == "superseded"
    assert "healthy again" in (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "autopilot_recommendation_superseded")
        .first()
        .payload["reason"]
    )


def test_drift_on_draft_mapping_creates_refresh_rec(db, two_conns):
    src, tgt = two_conns
    m = Mapping(name="Drafty", source_id=src.id, target_id=tgt.id, status="draft")
    db.add(m)
    db.flush()
    db.add(DriftEvent(
        connection_id=src.id, snapshot_id=_snapshot(db, src.id),
        tables_added=["new_table"], tables_removed=[],
        columns_added=[{"table": "t", "column": "c"}], columns_removed=[],
        type_changes=[],
    ))
    db.commit()

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 1
    rec = _recs(db, "mapping_suggestions_refresh", "pending")[0]
    assert rec.payload == {"mapping_id": m.id}
    assert rec.confidence == 80.0  # additions present
    assert "Drafty" in rec.rationale["summary"]
    assert rec.rationale["trigger"]["kind"] == "schema_drift"


def test_drift_ignores_published_mappings(db, two_conns):
    src, tgt = two_conns
    db.add(Mapping(name="Pub", source_id=src.id, target_id=tgt.id, status="published"))
    db.flush()
    db.add(DriftEvent(
        connection_id=src.id, snapshot_id=_snapshot(db, src.id),
        tables_added=["x"], tables_removed=[], columns_added=[],
        columns_removed=[], type_changes=[],
    ))
    db.commit()

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 0
    assert _recs(db, "mapping_suggestions_refresh") == []


def test_publishing_mapping_supersedes_open_refresh_rec(db, two_conns):
    src, tgt = two_conns
    m = Mapping(name="Drafty2", source_id=src.id, target_id=tgt.id, status="draft")
    db.add(m)
    db.flush()
    db.add(DriftEvent(
        connection_id=src.id, snapshot_id=_snapshot(db, src.id),
        tables_added=["x"], tables_removed=[], columns_added=[],
        columns_removed=[], type_changes=[],
    ))
    db.commit()
    AutopilotEngine.evaluate_all(db)
    assert len(_recs(db, "mapping_suggestions_refresh", "pending")) == 1

    m.status = "published"
    db.commit()
    counts = AutopilotEngine.evaluate_all(db)
    assert counts["superseded"] == 1
    assert _recs(db, "mapping_suggestions_refresh")[0].status == "superseded"


def _snapshot(db, connection_id: int) -> int:
    """DriftEvent.snapshot_id is NOT NULL — create a minimal snapshot row."""
    from app.models.schema_snapshot import SchemaSnapshot
    snap = SchemaSnapshot(
        connection_id=connection_id, connection_name=f"conn-{connection_id}",
        schema_hash="h", schema_json={},
    )
    db.add(snap)
    db.flush()
    return snap.id


# ── bugs/02 + bugs/04: sweep fail-safety + bounded drift query ────────────


def test_bug02_unknown_draft_type_skipped_not_fatal(db, two_conns, monkeypatch):
    """bugs/02: one evaluator emitting a non-registry action type must not
    take down the whole sweep — valid drafts still land."""
    src, _ = two_conns
    src.health_status = "down"
    db.commit()

    monkeypatch.setattr(
        AutopilotEngine, "_evaluate_schema_drift",
        staticmethod(lambda db: [{
            "action_type": "not_in_registry_yet",
            "subject": "connection:999",
            "payload": {"connection_id": 999},
            "confidence": 50.0,
            "rationale": {"summary": "stale evaluator", "evidence": [],
                          "trigger": {}},
        }]),
    )

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 1   # the valid health rec
    assert counts["skipped"] == 1   # the bogus draft, skipped not fatal
    recs = _recs(db)
    assert len(recs) == 1
    assert recs[0].action_type == "connector_health_check"


def test_bug04_only_newest_drift_event_per_connection_is_used(db, two_conns):
    """bugs/04: latest-per-connection is computed in SQL; older events in the
    window neither duplicate recs nor win over the newest event."""
    src, tgt = two_conns
    m = Mapping(name="MultiDrift", source_id=src.id, target_id=tgt.id,
                status="draft")
    db.add(m)
    db.flush()
    event_ids = []
    for i in range(3):
        ev = DriftEvent(
            connection_id=src.id, snapshot_id=_snapshot(db, src.id),
            tables_added=[f"t{i}"], tables_removed=[], columns_added=[],
            columns_removed=[], type_changes=[],
        )
        db.add(ev)
        db.flush()
        event_ids.append(ev.id)
    db.commit()

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 1
    rec = _recs(db, "mapping_suggestions_refresh")[0]
    newest = max(event_ids)
    assert f"drift_event_id={newest}" in rec.rationale["evidence"]
    for older in event_ids[:-1]:
        assert f"drift_event_id={older}" not in rec.rationale["evidence"]


# ── mapping-suggestions precompute (2026-08-01 design) ─────────────


def test_new_draft_mapping_creates_refresh_rec(db, two_conns):
    src, tgt = two_conns
    m = Mapping(name="Fresh", source_id=src.id, target_id=tgt.id, status="draft")
    db.add(m)
    db.commit()

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 1
    rec = _recs(db, "mapping_suggestions_refresh", "pending")[0]
    assert rec.payload == {"mapping_id": m.id}
    assert rec.confidence == 85.0
    assert "Fresh" in rec.rationale["summary"]
    assert rec.rationale["trigger"]["kind"] == "mapping_created"


def test_new_draft_mapping_with_suggestions_already_requested_is_skipped(db, two_conns):
    src, tgt = two_conns
    m = Mapping(
        name="Already Warmed", source_id=src.id, target_id=tgt.id,
        status="draft", pending_suggestion_task_id="task-123",
    )
    db.add(m)
    db.commit()

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 0
    assert _recs(db, "mapping_suggestions_refresh") == []


def test_drift_and_new_mapping_in_same_sweep_prefers_drift_rationale(db, two_conns):
    """A mapping that is both brand new and already drift-affected in the
    same sweep must end up with schema_drift's rationale, not mapping_created's
    generic one — asserted end-to-end here (see also
    test_merge_prefers_schema_drift_over_mapping_created for the unit-level
    guarantee that doesn't depend on evaluator call order)."""
    src, tgt = two_conns
    m = Mapping(name="NewAndDrifted", source_id=src.id, target_id=tgt.id, status="draft")
    db.add(m)
    db.flush()
    db.add(DriftEvent(
        connection_id=src.id, snapshot_id=_snapshot(db, src.id),
        tables_added=["new_table"], tables_removed=[],
        columns_added=[{"table": "t", "column": "c"}], columns_removed=[],
        type_changes=[],
    ))
    db.commit()

    counts = AutopilotEngine.evaluate_all(db)
    assert counts["created"] == 1
    rec = _recs(db, "mapping_suggestions_refresh", "pending")[0]
    assert rec.rationale["trigger"]["kind"] == "schema_drift"
    assert rec.confidence == 80.0


def test_merge_prefers_schema_drift_over_mapping_created():
    mapping_created = {
        "action_type": "mapping_suggestions_refresh", "subject": "mapping:1",
        "payload": {"mapping_id": 1}, "confidence": 85.0,
        "rationale": {"summary": "new", "evidence": [],
                      "trigger": {"kind": "mapping_created"}},
    }
    schema_drift = {
        "action_type": "mapping_suggestions_refresh", "subject": "mapping:1",
        "payload": {"mapping_id": 1}, "confidence": 80.0,
        "rationale": {"summary": "drift", "evidence": [],
                      "trigger": {"kind": "schema_drift"}},
    }

    for drafts in ([mapping_created, schema_drift], [schema_drift, mapping_created]):
        merged = AutopilotEngine._merge_drafts_by_subject(drafts)
        assert len(merged) == 1
        assert merged[0]["rationale"]["trigger"]["kind"] == "schema_drift"
