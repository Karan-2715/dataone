"""suggest_mappings_task re-run semantics.

The suggester must behave like a DBA who remembers their own decisions:
- a *pending* suggestion is an open question — re-running the task must
  not duplicate it;
- a *rejected* (source → target) pair is a decision already made — the
  exact same match is never offered again, though a different source for
  that target column still can be.
"""
from __future__ import annotations

import pytest

from app.models.mapping import AISuggestion
from app.services.mapping_service import MappingService
from app.workers import mapping_tasks as mt


class _NoCloseSession:
    """Proxy that hands the task the test session but ignores close()."""

    def __init__(self, s):
        self._s = s

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._s, name)


def _fake_schema(_conn):
    return {
        "t1": [{"name": "c1", "type": "TEXT"}],
        "t2": [{"name": "c2", "type": "TEXT"}],
    }


def _fake_match(*, source_name, source_schema, target_name, target_schema):
    """Suggest same-named columns at confidence 90."""
    src_names = {c["name"] for c in source_schema}
    return {
        "matches": [
            {
                "source": c["name"], "target": c["name"],
                "confidence": 90, "reason": "name match",
                "components": {"name_similarity": 100, "type_compatibility": 100},
            }
            for c in target_schema if c["name"] in src_names
        ],
    }


@pytest.fixture()
def patched_task_env(db, monkeypatch):
    monkeypatch.setattr(mt, "SessionLocal", lambda: _NoCloseSession(db))
    monkeypatch.setattr(
        mt.SchemaService, "get_full_schema", staticmethod(_fake_schema),
    )
    monkeypatch.setattr(
        mt.AIService, "match_schemas", staticmethod(_fake_match),
    )


def _make_mapping(db, admin, seeded_connections):
    src, tgt = seeded_connections
    return MappingService.create_mapping(
        db, source_id=src.id, target_id=tgt.id,
        name="M", actor=admin.email,
    )


def test_rerun_does_not_duplicate_pending_suggestions(
    db, admin, seeded_connections, patched_task_env,
):
    m = _make_mapping(db, admin, seeded_connections)

    first = mt.suggest_mappings_task.run(mapping_id=m.id)
    assert first["status"] == "completed"
    assert first["suggestions_created"] == 2  # t1.c1 and t2.c2

    second = mt.suggest_mappings_task.run(mapping_id=m.id)
    assert second["status"] == "completed"
    assert second["suggestions_created"] == 0

    total = (
        db.query(AISuggestion)
        .filter(AISuggestion.mapping_id == m.id)
        .count()
    )
    assert total == 2
    stored = db.query(AISuggestion).filter(AISuggestion.mapping_id == m.id).first()
    # historical_mapping (E14-3) is 0 here — no prior decided suggestions
    # exist yet for this exact (source_column, target_column) pair.
    assert stored.components == {
        "name_similarity": 100,
        "type_compatibility": 100,
        "historical_mapping": 0.0,
    }
    assert stored.suggested_transformation == {"kind": "direct"}


def test_rerun_never_reoffers_a_rejected_match(
    db, admin, seeded_connections, patched_task_env,
):
    m = _make_mapping(db, admin, seeded_connections)

    first = mt.suggest_mappings_task.run(mapping_id=m.id)
    assert first["suggestions_created"] == 2

    # DBA rejects the t1.c1 → t1.c1 match.
    rejected = (
        db.query(AISuggestion)
        .filter(
            AISuggestion.mapping_id == m.id,
            AISuggestion.target_table == "t1",
            AISuggestion.target_column == "c1",
        )
        .one()
    )
    MappingService.reject_suggestion(db, m.id, rejected.id, actor=admin.email)

    # Re-run: t1.c1 is unmapped and has no pending suggestion, but its only
    # candidate match was rejected — it must NOT come back. t2.c2 is still
    # pending, so it must not be duplicated either.
    rerun = mt.suggest_mappings_task.run(mapping_id=m.id)
    assert rerun["status"] == "completed"
    assert rerun["suggestions_created"] == 0

    statuses = [
        s.status for s in
        db.query(AISuggestion).filter(AISuggestion.mapping_id == m.id).all()
    ]
    assert sorted(statuses) == ["pending", "rejected"]


def test_hallucinated_source_column_is_not_persisted(
    db, admin, seeded_connections, patched_task_env, monkeypatch,
):
    m = _make_mapping(db, admin, seeded_connections)
    monkeypatch.setattr(mt.AIService, "match_schemas", staticmethod(
        lambda **kwargs: {"matches": [{
            "source": "invented_column",
            "target": kwargs["target_schema"][0]["name"],
            "confidence": 99,
            "components": {},
        }]},
    ))
    result = mt.suggest_mappings_task.run(mapping_id=m.id)
    assert result["status"] == "completed"
    assert result["suggestions_created"] == 0
    assert db.query(AISuggestion).filter(AISuggestion.mapping_id == m.id).count() == 0


# ── pending_suggestion_task_id is always released (uiux bug report: AI
#    suggestion generation "is not idempotent") ───────────────────────────


def test_finally_clears_pending_task_id_on_success(
    db, admin, seeded_connections, patched_task_env,
):
    m = _make_mapping(db, admin, seeded_connections)
    m.pending_suggestion_task_id = "task-in-flight"
    db.commit()

    result = mt.suggest_mappings_task.run(mapping_id=m.id)
    assert result["status"] == "completed"

    db.refresh(m)
    assert m.pending_suggestion_task_id is None


def test_finally_clears_pending_task_id_on_schema_fetch_failure(
    db, admin, seeded_connections, patched_task_env, monkeypatch,
):
    """A failure path that `return`s early (not an exception) must still
    release the guard — otherwise a schema-fetch outage permanently wedges
    the "AI Suggest" button for that mapping."""
    m = _make_mapping(db, admin, seeded_connections)
    m.pending_suggestion_task_id = "task-in-flight"
    db.commit()

    def _boom(_conn):
        raise RuntimeError("connector unreachable")

    monkeypatch.setattr(mt.SchemaService, "get_full_schema", staticmethod(_boom))

    result = mt.suggest_mappings_task.run(mapping_id=m.id)
    assert result["status"] == "failed"

    db.refresh(m)
    assert m.pending_suggestion_task_id is None


def test_finally_clears_pending_task_id_on_unexpected_exception(
    db, admin, seeded_connections, patched_task_env, monkeypatch,
):
    """`AIService.match_schemas` failures are caught per-(source, target)
    table pair and skipped (see the task's own per-call try/except) — to
    exercise the outer exception handler this needs a failure somewhere
    that ISN'T already caught, e.g. transformation proposal."""
    m = _make_mapping(db, admin, seeded_connections)
    m.pending_suggestion_task_id = "task-in-flight"
    db.commit()

    def _boom(*args, **kwargs):
        raise RuntimeError("transformation proposer exploded")

    monkeypatch.setattr(mt, "propose_transformations", _boom)

    result = mt.suggest_mappings_task.run(mapping_id=m.id)
    assert result["status"] == "failed"

    db.refresh(m)
    assert m.pending_suggestion_task_id is None
