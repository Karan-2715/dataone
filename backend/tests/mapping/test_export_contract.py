"""Contract test for GET /api/v1/mappings/{id}/export.

Locks the published artifact JSON shape to docs/mapper-mapping-contract.md so
the Pipelines team can rely on it. Any drift from the contract breaks the
build. Treat as a living spec: when the contract evolves intentionally,
update both this file and the contract doc in the same commit.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.services import schema_service
from app.services.mapping_service import MappingService


# ── The 11 transformation kinds — keep in sync with the contract doc. ───
ALLOWED_TRANSFORMATION_KINDS = frozenset({
    "direct", "cast", "concat", "substring", "coalesce",
    "upper", "lower", "trim", "default", "null_if", "lookup",
})


def _fake_schema(_conn):
    return {
        "t1": [
            {"name": "c1", "type": "TEXT"},
            {"name": "c2", "type": "INTEGER"},
            {"name": "c3", "type": "TIMESTAMP"},
        ],
    }


def _publish_with_edges(db, admin, src, tgt, edges):
    """Helper: create mapping, add edges, publish, return (mapping, version)."""
    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        schema_service.SchemaService, "get_full_schema",
        staticmethod(_fake_schema),
    )
    try:
        m = MappingService.create_mapping(
            db, source_id=src.id, target_id=tgt.id, name="Contract", actor=admin.email,
        )
        for edge in edges:
            MappingService.add_edge(
                db, m.id,
                target=edge["target"],
                sources=edge["sources"],
                transformation=edge["transformation"],
                origin=edge.get("origin", "manual"),
                actor=admin.email,
            )
        v = MappingService.publish(db, m.id, actor=admin.email)
        return m, v
    finally:
        monkey.undo()


def test_export_top_level_shape(db, admin, seeded_connections):
    """Every top-level field documented in §2 of the contract must exist."""
    src, tgt = seeded_connections
    m, v = _publish_with_edges(db, admin, src, tgt, [
        {
            "target": {"table": "t1", "column": "c1", "type": "TEXT", "nullable": False},
            "sources": [{"table": "s1", "column": "c1", "type": "TEXT", "nullable": False}],
            "transformation": {"kind": "direct"},
        },
    ])

    artifact = MappingService.export_json(db, m.id, actor=admin.email)

    # Required top-level keys.
    for key in (
        "mapping_id", "name", "version", "status",
        "published_at", "published_by",
        "source", "target", "field_mappings", "schema_snapshot",
    ):
        assert key in artifact, f"missing top-level field '{key}'"

    # Type + value assertions.
    assert artifact["mapping_id"] == m.id
    assert artifact["name"] == "Contract"
    assert artifact["version"] == v.version_number
    assert artifact["version"] == 1
    assert artifact["status"] == "published"
    assert artifact["published_by"] == admin.email
    # published_at must be ISO 8601 and parseable.
    assert isinstance(artifact["published_at"], str)
    datetime.fromisoformat(artifact["published_at"])  # raises if invalid
    # Source / target connection metadata.
    assert artifact["source"] == {
        "connection_id": src.id,
        "name": src.name,
        "type": src.type,
    }
    assert artifact["target"] == {
        "connection_id": tgt.id,
        "name": tgt.name,
        "type": tgt.type,
    }
    # Schema snapshot is captured at publish time.
    assert "source" in artifact["schema_snapshot"]
    assert "target" in artifact["schema_snapshot"]


def test_export_field_mapping_entry_shape(db, admin, seeded_connections):
    """Each field_mappings entry must conform to §3 of the contract."""
    src, tgt = seeded_connections
    m, _ = _publish_with_edges(db, admin, src, tgt, [
        {
            "target": {"table": "t1", "column": "c1", "type": "TEXT", "nullable": False},
            "sources": [{"table": "s1", "column": "c1", "type": "TEXT", "nullable": False}],
            "transformation": {"kind": "cast", "from": "TEXT", "to": "VARCHAR"},
        },
    ])
    artifact = MappingService.export_json(db, m.id, actor=admin.email)
    assert len(artifact["field_mappings"]) == 1
    fm = artifact["field_mappings"][0]

    # Required field_mapping entry keys.
    for key in ("id", "origin", "target", "sources", "transformation", "audit"):
        assert key in fm, f"missing field_mappings[0].{key}"

    # Origin must be one of the three documented values.
    assert fm["origin"] in {"manual", "ai_accepted", "english_parsed"}
    # ai_confidence may be null for non-AI edges.
    assert fm["ai_confidence"] is None or isinstance(fm["ai_confidence"], (int, float))

    # Target object shape.
    for key in ("table", "column", "type", "nullable", "primary_key"):
        assert key in fm["target"], f"missing field_mappings[0].target.{key}"
    assert fm["target"]["table"] == "t1"
    assert fm["target"]["column"] == "c1"
    assert fm["target"]["type"] == "TEXT"
    assert fm["target"]["nullable"] is False
    assert fm["target"]["primary_key"] is False

    # Sources is a non-empty list of objects with table/column/type/nullable.
    assert isinstance(fm["sources"], list) and len(fm["sources"]) >= 1
    for s in fm["sources"]:
        for key in ("table", "column", "type", "nullable"):
            assert key in s

    # Transformation kind is one of the 11 allow-listed values.
    assert fm["transformation"]["kind"] in ALLOWED_TRANSFORMATION_KINDS
    # Cast transform carries from/to.
    assert fm["transformation"]["from"] == "TEXT"
    assert fm["transformation"]["to"] == "VARCHAR"

    # Audit object carries created_by/created_at/updated_by/updated_at.
    for key in ("created_by", "created_at", "updated_by", "updated_at"):
        assert key in fm["audit"], f"missing field_mappings[0].audit.{key}"
    assert fm["audit"]["created_by"] == admin.email


def test_export_ai_accepted_edge_carries_confidence(db, admin, seeded_connections):
    """AI-accepted edges must report ai_confidence in [0.0, 1.0]."""
    from app.models.mapping import AISuggestion

    src, tgt = seeded_connections
    m = MappingService.create_mapping(
        db, source_id=src.id, target_id=tgt.id, name="AI Edge", actor=admin.email,
    )
    sug = AISuggestion(
        mapping_id=m.id,
        target_table="t1", target_column="c1", target_type="TEXT",
        source_table="s1", source_column="c1", source_type="TEXT",
        confidence=92.0, reason="test", status="pending",
    )
    db.add(sug)
    db.commit()
    db.refresh(sug)
    MappingService.accept_suggestion(
        db, m.id, sug.id, {"kind": "direct"}, actor=admin.email,
    )

    # Add a compatible second edge so the publish gate passes, then publish.
    MappingService.add_edge(
        db, m.id,
        target={"table": "t1", "column": "c2", "type": "INTEGER", "nullable": False},
        sources=[{"table": "s1", "column": "c2", "type": "INTEGER", "nullable": False}],
        transformation={"kind": "direct"},
        actor=admin.email,
    )
    # Stub the schema fetch before publishing.
    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        schema_service.SchemaService, "get_full_schema",
        staticmethod(_fake_schema),
    )
    try:
        MappingService.publish(db, m.id, actor=admin.email)
    finally:
        monkey.undo()

    artifact = MappingService.export_json(db, m.id, actor=admin.email)
    ai_edges = [fm for fm in artifact["field_mappings"] if fm["origin"] == "ai_accepted"]
    assert len(ai_edges) >= 1
    for fm in ai_edges:
        assert fm["ai_confidence"] is not None
        assert 0.0 <= fm["ai_confidence"] <= 1.0


def test_export_supports_version_pinning(db, admin, seeded_connections):
    """version_id query parameter pins the export to a specific version."""
    src, tgt = seeded_connections
    m, v1 = _publish_with_edges(db, admin, src, tgt, [
        {
            "target": {"table": "t1", "column": "c1", "type": "TEXT", "nullable": False},
            "sources": [{"table": "s1", "column": "c1", "type": "TEXT", "nullable": False}],
            "transformation": {"kind": "direct"},
        },
    ])
    # Reopen to draft (E13-6) and publish v2.
    MappingService.create_draft_revision(db, m.id, v1.id, actor=admin.email)
    MappingService.add_edge(
        db, m.id,
        target={"table": "t1", "column": "c2", "type": "INTEGER", "nullable": False},
        sources=[{"table": "s1", "column": "c2", "type": "INTEGER", "nullable": False}],
        transformation={"kind": "cast", "from": "INTEGER", "to": "BIGINT"},
        actor=admin.email,
    )
    v2 = MappingService.publish(db, m.id, actor=admin.email)

    # Latest export = v2.
    latest = MappingService.export_json(db, m.id, actor=admin.email)
    assert latest["version"] == 2
    assert len(latest["field_mappings"]) == 2

    # Pin to v1 via version_id.
    pinned = MappingService.export_json(db, m.id, actor=admin.email, version_id=v1.id)
    assert pinned["version"] == 1
    assert len(pinned["field_mappings"]) == 1
    assert pinned["field_mappings"][0]["target"]["column"] == "c1"


def test_export_rejects_unknown_version(db, admin, seeded_connections):
    """version_id referring to a non-existent version raises 404."""
    from fastapi import HTTPException

    src, tgt = seeded_connections
    m, _ = _publish_with_edges(db, admin, src, tgt, [
        {
            "target": {"table": "t1", "column": "c1", "type": "TEXT", "nullable": False},
            "sources": [{"table": "s1", "column": "c1", "type": "TEXT", "nullable": False}],
            "transformation": {"kind": "direct"},
        },
    ])
    with pytest.raises(HTTPException) as e:
        MappingService.export_json(db, m.id, actor=admin.email, version_id=99999)
    assert e.value.status_code == 404


def test_export_handles_multi_source_edge(db, admin, seeded_connections):
    """N:1 edges expose all source columns in `sources`."""
    src, tgt = seeded_connections
    m, _ = _publish_with_edges(db, admin, src, tgt, [
        {
            "target": {"table": "t1", "column": "c1", "type": "TEXT", "nullable": False},
            "sources": [
                {"table": "s1", "column": "first_name", "type": "TEXT", "nullable": False},
                {"table": "s1", "column": "last_name", "type": "TEXT", "nullable": False},
            ],
            "transformation": {"kind": "concat", "parts": [
                {"kind": "source"}, {"kind": "literal", "value": " "}, {"kind": "source"},
            ]},
        },
    ])
    artifact = MappingService.export_json(db, m.id, actor=admin.email)
    fm = artifact["field_mappings"][0]
    assert len(fm["sources"]) == 2
    assert fm["transformation"]["kind"] == "concat"
    assert len(fm["transformation"]["parts"]) == 3


def test_export_emits_audit_event(db, admin, seeded_connections):
    """Every export call records an AuditLog row with the right payload."""
    src, tgt = seeded_connections
    m, _ = _publish_with_edges(db, admin, src, tgt, [
        {
            "target": {"table": "t1", "column": "c1", "type": "TEXT", "nullable": False},
            "sources": [{"table": "s1", "column": "c1", "type": "TEXT", "nullable": False}],
            "transformation": {"kind": "direct"},
        },
    ])
    from app.models.audit import AuditLog
    # Clear audit log so we only see the export.
    db.query(AuditLog).filter(AuditLog.event_type == "mapping_exported").delete()
    db.commit()

    MappingService.export_json(db, m.id, actor=admin.email)
    audit = db.query(AuditLog).filter(AuditLog.event_type == "mapping_exported").first()
    assert audit is not None
    assert audit.payload["mapping_id"] == m.id
    assert audit.payload["version_number"] == 1
    assert "version_id" in audit.payload
