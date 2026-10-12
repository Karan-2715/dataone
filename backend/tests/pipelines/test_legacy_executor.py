"""Regression tests for the legacy synchronous graph executor (Bug #12).

The Task #1 refactor accidentally deleted the executor's helper methods
and left `execute_pipeline` returning an undefined variable — and nothing
caught it because `/execute` had zero coverage. These tests pin the
executor's contract (dict envelope, graph validation errors) until
Task #3 replaces it wholesale.
"""
from __future__ import annotations

import pytest

from app.services import pipeline_service
from app.services.pipeline_service import PipelineService


def _node(node_id, node_type, connection_id=None):
    cfg = {"connection_id": connection_id} if connection_id is not None else None
    return {"id": node_id, "type": node_type, "config": cfg}


class _FakeConn:
    def __init__(self, name, conn_type="postgres"):
        self.name = name
        self.type = conn_type
        self.config = {}


def test_execute_pipeline_rejects_missing_source_node():
    with pytest.raises(ValueError, match="source node"):
        PipelineService.execute_pipeline(
            nodes=[_node("t", "target", 2)], edges=[],
        )


def test_execute_pipeline_rejects_unknown_node_type():
    with pytest.raises(ValueError, match="Unknown node type"):
        PipelineService.execute_pipeline(
            nodes=[_node("s", "source", 1), _node("x", "banana"), _node("t", "target", 2)],
            edges=[],
        )


def test_execute_pipeline_rejects_disconnected_graph():
    with pytest.raises(ValueError, match="not connected"):
        PipelineService.execute_pipeline(
            nodes=[_node("s", "source", 1), _node("t", "target", 2)],
            edges=[],  # no edge from s to t
        )


def test_execute_pipeline_returns_dict_envelope(monkeypatch):
    """Happy path (no ai_matcher, non-SQLite target): the executor must
    return the dict envelope the router consumes — this is exactly what
    the Bug #12 regression broke (tuple return of an undefined name)."""
    monkeypatch.setattr(
        PipelineService, "_load_connections",
        staticmethod(lambda sid, tid: (_FakeConn("Src"), _FakeConn("Tgt"))),
    )
    monkeypatch.setattr(
        pipeline_service.SchemaService, "get_full_schema",
        staticmethod(lambda _conn: {"users": [{"name": "id", "type": "INTEGER"}]}),
    )
    monkeypatch.setattr(
        pipeline_service.SchemaMapperService, "generate_migration_sql",
        staticmethod(lambda mappings, target_db_type: {
            "ddl": [], "dml": [], "warnings": [], "total_statements": 0,
        }),
    )
    result = PipelineService.execute_pipeline(
        nodes=[_node("s", "source", 1), _node("t", "target", 2)],
        edges=[{"id": "e1", "source": "s", "target": "t"}],
    )
    assert isinstance(result, dict)
    assert result["status"] == "success"
    assert result["source"] == "Src"
    assert result["target"] == "Tgt"
    assert result["rows_copied"] == {}
    assert result["total_rows_copied"] == 0
    assert "table_mappings" in result
    assert "unmatched_source" in result
    assert "unmatched_target" in result
    assert "migration_sql" in result


def test_execute_pipeline_identity_matching_via_on_the_fly_target(monkeypatch):
    """SQLite target with empty schema triggers on-the-fly creation +
    identity matching; the envelope must report matched tables."""
    monkeypatch.setattr(
        PipelineService, "_load_connections",
        staticmethod(lambda sid, tid: (_FakeConn("Src", "sqlite"), _FakeConn("Tgt", "sqlite"))),
    )
    schemas = {
        "Src": {"users": [{"name": "id", "type": "INTEGER"}]},
        "Tgt": {},
    }
    monkeypatch.setattr(
        pipeline_service.SchemaService, "get_full_schema",
        staticmethod(lambda conn: schemas[conn.name]),
    )
    monkeypatch.setattr(
        pipeline_service.SchemaMapperService, "generate_migration_sql",
        staticmethod(lambda mappings, target_db_type: {
            "ddl": [], "dml": [], "warnings": [], "total_statements": 0,
        }),
    )
    monkeypatch.setattr(
        PipelineService, "_execute_target_migration",
        staticmethod(lambda s, t, tm, ddl: {"users": 3}),
    )
    result = PipelineService.execute_pipeline(
        nodes=[_node("s", "source", 1), _node("t", "target", 2)],
        edges=[{"id": "e1", "source": "s", "target": "t"}],
    )
    assert result["status"] == "success"
    assert len(result["table_mappings"]) == 1
    assert result["table_mappings"][0]["source_table"] == "users"
    assert result["total_rows_copied"] == 3
