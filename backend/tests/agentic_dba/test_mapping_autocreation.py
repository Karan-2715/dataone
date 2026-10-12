"""Draft-mapping auto-creation tests (agentic_dba_tasks #8)."""
from __future__ import annotations

from app.models.audit import AuditLog
from app.models.mapping import FieldMapping, Mapping
from app.models.schema_design_plan import SchemaDesignPlan
from app.services.agentic_dba_execution_service import approve_and_execute_plan


def _plan(db, retail_connection, target_connection, admin, *, transformations):
    plan = SchemaDesignPlan(
        question="synthetic", source_connection_id=retail_connection.id,
        target_connection_id=(target_connection.id if target_connection else None),
        status="ready", dialect="sqlite", created_by=admin.email,
        generated_ddl=[{"table": "dim_customers", "mode": "create",
                        "statements": [
                            "CREATE TABLE dim_customers (customer_key INTEGER PRIMARY KEY, name TEXT, email TEXT)"
                        ]}],
        transformations=transformations,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


TRANSFORMS = [
    {"target_table": "dim_customers", "target_column": "name", "target_type": "TEXT",
     "target_nullable": True, "sources": [{"table": "customers", "column": "name"}],
     "transformation": {"kind": "direct"}, "note": None},
    {"target_table": "dim_customers", "target_column": "email", "target_type": "TEXT",
     "target_nullable": True, "sources": [{"table": "customers", "column": "email"}],
     "transformation": None,  # unresolved — must NOT become a wrong edge
     "note": "author manually"},
]


def test_draft_mapping_created_with_distinct_target(db, retail_connection,
                                                    target_connection, admin):
    plan = _plan(db, retail_connection, target_connection, admin,
                 transformations=TRANSFORMS)
    plan = approve_and_execute_plan(db, plan.id, actor=admin.email, role="admin")

    assert plan.status == "applied"
    assert plan.created_mapping_id is not None
    mapping = db.query(Mapping).filter(Mapping.id == plan.created_mapping_id).one()
    assert mapping.status == "draft"  # ordinary draft — existing lifecycle
    assert mapping.source_id == retail_connection.id
    assert mapping.target_id == target_connection.id

    edges = db.query(FieldMapping).filter(FieldMapping.mapping_id == mapping.id).all()
    assert len(edges) == 1  # only the resolved transformation became an edge
    edge = edges[0]
    assert edge.target_column == "name"
    assert edge.origin == "agentic_dba"  # distinguishable from manual edges
    assert edge.transformation == {"kind": "direct"}

    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "agentic_dba.mapping_autocreated")
        .one()
    )
    assert row.event_metadata["mapping_id"] == mapping.id
    assert row.event_metadata["edges_added"] == 1


def test_same_connection_plan_skips_mapping_with_honest_note(db, retail_connection, admin):
    plan = _plan(db, retail_connection, None, admin, transformations=TRANSFORMS)
    plan = approve_and_execute_plan(db, plan.id, actor=admin.email, role="admin")

    assert plan.status == "applied"
    assert plan.created_mapping_id is None
    assert any("distinct source/target connections" in n
               for n in plan.confidence_notes or [])
    assert db.query(Mapping).count() == 0


def test_no_transformations_means_no_mapping(db, retail_connection, target_connection, admin):
    plan = _plan(db, retail_connection, target_connection, admin, transformations=[])
    plan = approve_and_execute_plan(db, plan.id, actor=admin.email, role="admin")
    assert plan.created_mapping_id is None
    assert db.query(Mapping).count() == 0
