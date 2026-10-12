"""Collision + per-object tracking tests (agentic_dba_tasks #9)."""
from __future__ import annotations

from app.models.schema_design_plan import SchemaDesignPlan
from app.services.agentic_dba_engine import create_plan, generate_plan
from app.services.agentic_dba_execution_service import approve_and_execute_plan


def test_collision_routes_to_migration_not_create(db, retail_connection, admin):
    """A proposed table name that already exists in the catalog must get an
    ALTER-based migration, never a blind CREATE TABLE."""
    from app.models.schema_catalog import CatalogColumn, CatalogTable

    # Make the template's dim_customers collide with an existing table.
    t = CatalogTable(connection_id=retail_connection.id, table_name="dim_customers")
    db.add(t)
    db.flush()
    db.add(CatalogColumn(table_id=t.id, column_name="customer_key", data_type="INTEGER",
                         nullable=False, is_primary_key=True, ordinal_position=0))
    db.commit()

    plan = generate_plan(db, create_plan(
        db, question="create retail analytics target schemas based on profiling",
        connection_id=retail_connection.id, session_id=None, actor=admin.email).id)

    ddl = next(d for d in plan.generated_ddl if d["table"] == "dim_customers")
    assert ddl["mode"] == "migrate"
    assert all(s.startswith("ALTER TABLE dim_customers ADD COLUMN") or s.startswith("--")
               for s in ddl["statements"])
    assert any("already exists" in n and "dim_customers" in n
               for n in plan.confidence_notes)
    # Non-colliding tables still get plain CREATEs.
    others = [d for d in plan.generated_ddl if d["table"] != "dim_customers"]
    assert others and all(d["mode"] == "create" for d in others)


def test_mid_plan_failure_stops_and_reports_per_object(db, retail_connection, admin):
    """Table 2 of 3 fails -> [applied, failed, skipped], plan
    partially_applied — never an opaque all-or-nothing result."""
    plan = SchemaDesignPlan(
        question="synthetic", source_connection_id=retail_connection.id,
        status="ready", dialect="sqlite", created_by=admin.email,
        generated_ddl=[
            {"table": "good_one", "mode": "create",
             "statements": ["CREATE TABLE good_one (id INTEGER PRIMARY KEY)"]},
            {"table": "bad_one", "mode": "create",
             "statements": ["CREATE TABLE bad_one ("]},  # syntax error
            {"table": "never_reached", "mode": "create",
             "statements": ["CREATE TABLE never_reached (id INTEGER)"]},
        ],
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    plan = approve_and_execute_plan(db, plan.id, actor=admin.email, role="admin")
    assert plan.status == "partially_applied"
    by_table = {r["table"]: r for r in plan.apply_results}
    assert by_table["good_one"]["status"] == "applied"
    assert by_table["bad_one"]["status"] == "failed"
    assert by_table["bad_one"]["error"]
    assert by_table["never_reached"]["status"] == "skipped"


def test_all_objects_failing_marks_plan_failed(db, retail_connection, admin):
    plan = SchemaDesignPlan(
        question="synthetic", source_connection_id=retail_connection.id,
        status="ready", dialect="sqlite", created_by=admin.email,
        generated_ddl=[{"table": "broken", "mode": "create",
                        "statements": ["CREATE TABLE broken ("]}],
    )
    db.add(plan)
    db.commit()
    plan = approve_and_execute_plan(db, plan.id, actor=admin.email, role="admin")
    assert plan.status == "failed"


def test_sqlite_unsupported_alter_recorded_as_comment_never_executed(db, retail_connection, admin):
    """Type changes on SQLite are surfaced as comments (generate_migration_sql
    precedent) and must be skipped, not executed, at apply time."""
    plan = SchemaDesignPlan(
        question="synthetic", source_connection_id=retail_connection.id,
        status="ready", dialect="sqlite", created_by=admin.email,
        generated_ddl=[{"table": "customers", "mode": "migrate",
                        "statements": [
                            "-- ALTER TABLE customers ALTER COLUMN email TYPE VARCHAR (unsupported in SQLite)",
                            "ALTER TABLE customers ADD COLUMN loyalty_tier TEXT",
                        ]}],
    )
    db.add(plan)
    db.commit()
    plan = approve_and_execute_plan(db, plan.id, actor=admin.email, role="admin")
    assert plan.status == "applied"
    result = plan.apply_results[0]
    assert result["status"] == "applied"
    assert result["statements_executed"] == 1  # the comment was skipped
