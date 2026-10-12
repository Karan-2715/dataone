"""Tests for the pipeline execution engine (Task #3).

Covers: a clean run that actually copies rows through real SQLite files,
a run blocked by drift, a run that fails on an unsupported (non-direct)
transformation, and a re-run producing no duplicate rows (upsert on the
natural key).

Mirrors the SessionLocal-patching pattern in
tests/mapping/test_suggest_task.py — PipelineExecutor.execute() opens its
own session via SessionLocal() (as the real Celery task does), so tests
monkeypatch that module-level name to hand it the test's own session.
"""
import sqlite3

import pytest

from app.models.pipeline import PipelineRun
from app.services import pipeline_executor as pe
from app.services.pipeline_service import PipelineCRUD


class _NoCloseSession:
    """Proxy that hands the executor the test session but ignores close()."""

    def __init__(self, s):
        self._s = s

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._s, name)


@pytest.fixture()
def patched_executor_env(db, monkeypatch):
    monkeypatch.setattr(pe, "SessionLocal", lambda: _NoCloseSession(db))


def _create_pipeline_and_run(db, admin, physical_sqlite_connections, mapping, trigger="manual", load_strategy=None):
    src, tgt = physical_sqlite_connections
    p = PipelineCRUD.create_pipeline(
        db, name="Exec Test", source_connection_id=src.id,
        target_connection_id=tgt.id, mapping_id=mapping.id, actor=admin.email,
        load_strategy=load_strategy,
    )
    run = PipelineRun(pipeline_id=p.id, status="pending", trigger=trigger)
    db.add(run)
    db.commit()
    db.refresh(run)
    return p, run


def test_execute_happy_path_copies_rows(
    db, admin, patched_executor_env, physical_sqlite_connections, seeded_mapping_with_field_mappings,
):
    mapping, _version = seeded_mapping_with_field_mappings
    p, run = _create_pipeline_and_run(db, admin, physical_sqlite_connections, mapping)

    result = pe.PipelineExecutor.execute(p.id, run.id, trigger="manual")

    assert result["status"] == "completed"
    assert result["rows_processed"] == 3

    _, tgt = physical_sqlite_connections
    conn = sqlite3.connect(tgt.config["path"])
    rows = conn.execute("SELECT cust_id, full_name, contact_email FROM customers ORDER BY cust_id").fetchall()
    conn.close()
    assert rows == [
        (1, "Alice", "alice@x.com"),
        (2, "Bob", "bob@x.com"),
        (3, "Cara", "cara@x.com"),
    ]

    db.refresh(run)
    assert run.status == "succeeded"
    assert run.rows_processed == 3
    assert run.finished_at is not None

    steps = db.query(pe.PipelineRunStep).filter(pe.PipelineRunStep.run_id == run.id).all()
    assert {s.step for s in steps} == {"extract", "transform", "load"}
    assert all(s.status == "succeeded" for s in steps)


def test_execute_rerun_upserts_no_duplicates(
    db, admin, patched_executor_env, physical_sqlite_connections, seeded_mapping_with_field_mappings,
):
    """A second run against the same pipeline must not duplicate rows —
    the natural key (cust_id, from the target's PK-flagged field mapping)
    drives an upsert, not a blind insert."""
    mapping, _version = seeded_mapping_with_field_mappings
    p, run1 = _create_pipeline_and_run(db, admin, physical_sqlite_connections, mapping)
    pe.PipelineExecutor.execute(p.id, run1.id, trigger="manual")

    run2 = PipelineRun(pipeline_id=p.id, status="pending", trigger="rerun", parent_run_id=run1.id)
    db.add(run2)
    db.commit()
    db.refresh(run2)
    result = pe.PipelineExecutor.execute(p.id, run2.id, trigger="rerun")

    assert result["status"] == "completed"
    _, tgt = physical_sqlite_connections
    conn = sqlite3.connect(tgt.config["path"])
    count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    conn.close()
    assert count == 3  # no duplicates


def test_execute_blocked_by_drift(
    db, admin, patched_executor_env, physical_sqlite_connections, seeded_mapping_with_field_mappings,
):
    mapping, _version = seeded_mapping_with_field_mappings
    p, run = _create_pipeline_and_run(db, admin, physical_sqlite_connections, mapping)

    src, _ = physical_sqlite_connections
    conn = sqlite3.connect(src.config["path"])
    conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    conn.commit()
    conn.close()

    result = pe.PipelineExecutor.execute(p.id, run.id, trigger="manual")

    assert result["status"] == "blocked"
    assert result["reason"] == "drift"
    db.refresh(run)
    assert run.status == "failed"
    assert "drift" in run.error_message.lower()


def test_execute_fails_clearly_on_unsupported_transformation(
    db, admin, patched_executor_env, physical_sqlite_connections, seeded_mapping_with_field_mappings,
):
    """A kind needing a live cross-table join (lookup) or multi-source
    binding (concat) must still fail the run with an actionable message —
    unlike single-source kinds (upper, cast, case, ...), see
    EXECUTABLE_TRANSFORM_KINDS docstring for why those two remain
    execution-blocked while every other kind now executes for real."""
    from app.models.mapping import FieldMapping

    mapping, version = seeded_mapping_with_field_mappings
    edge = db.query(FieldMapping).filter(
        FieldMapping.version_id == version.id, FieldMapping.target_column == "full_name",
    ).first()
    edge.transformation = {"kind": "lookup", "table": "names", "key_column": "id", "value_column": "name"}
    db.commit()

    p, run = _create_pipeline_and_run(db, admin, physical_sqlite_connections, mapping)
    result = pe.PipelineExecutor.execute(p.id, run.id, trigger="manual")

    assert result["status"] == "failed"
    assert "lookup" in result["error"].lower()
    assert "full_name" in result["error"]
    db.refresh(run)
    assert run.status == "failed"


def test_execute_applies_upper_transformation_for_real(
    db, admin, patched_executor_env, physical_sqlite_connections, seeded_mapping_with_field_mappings,
):
    """Single-source kinds beyond 'direct' now actually execute — not just
    preview — reusing transformation_preview_service's evaluator so a
    value that previews as X loads as X."""
    from app.models.mapping import FieldMapping

    mapping, version = seeded_mapping_with_field_mappings
    edge = db.query(FieldMapping).filter(
        FieldMapping.version_id == version.id, FieldMapping.target_column == "full_name",
    ).first()
    edge.transformation = {"kind": "upper"}
    db.commit()

    p, run = _create_pipeline_and_run(db, admin, physical_sqlite_connections, mapping)
    result = pe.PipelineExecutor.execute(p.id, run.id, trigger="manual")

    assert result["status"] == "completed"
    _, tgt = physical_sqlite_connections
    conn = sqlite3.connect(tgt.config["path"])
    rows = conn.execute("SELECT full_name FROM customers ORDER BY cust_id").fetchall()
    conn.close()
    assert rows == [("ALICE",), ("BOB",), ("CARA",)]


def test_execute_applies_case_transformation_for_real(
    db, admin, patched_executor_env, tmp_path,
):
    """The exact use case this transformation kind was built for: a
    threshold conditional (annual_revenue > 500M -> "Enterprise" else
    "SMB") applied to real rows during an actual pipeline run, not just
    previewed on sample data."""
    from app.connectors.sqlite import SQLiteConnector
    from app.models.connection import DBConnection
    from app.models.mapping import FieldMapping, Mapping, MappingVersion

    src_path = str(tmp_path / "case_src.db")
    tgt_path = str(tmp_path / "case_tgt.db")

    src_conn = sqlite3.connect(src_path)
    src_conn.execute("CREATE TABLE companies (id INTEGER PRIMARY KEY, annual_revenue INTEGER)")
    src_conn.executemany(
        "INSERT INTO companies (id, annual_revenue) VALUES (?, ?)",
        [(1, 800_000_000), (2, 50_000_000), (3, None)],
    )
    src_conn.commit()
    src_conn.close()

    tgt_conn = sqlite3.connect(tgt_path)
    tgt_conn.execute("CREATE TABLE company_segments (id INTEGER PRIMARY KEY, segment TEXT)")
    tgt_conn.commit()
    tgt_conn.close()

    src = DBConnection(name="CaseSrc", type="sqlite", config={"path": src_path})
    tgt = DBConnection(name="CaseTgt", type="sqlite", config={"path": tgt_path})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)

    # Drive the schema_snapshot from the connector itself (matches
    # seeded_mapping_with_field_mappings' own rationale) so it exactly
    # matches what validate_drift's live-schema fetch will compute —
    # an empty/hand-written snapshot 422s as "no source schema snapshot".
    src_connector = SQLiteConnector(src_path)
    tgt_connector = SQLiteConnector(tgt_path)
    source_schema = {t: src_connector.get_table_schema(t) for t in src_connector.get_tables()}
    target_schema = {t: tgt_connector.get_table_schema(t) for t in tgt_connector.get_tables()}
    src_connector.close()
    tgt_connector.close()

    m = Mapping(name="Case Map", source_id=src.id, target_id=tgt.id, status="published", created_by="test")
    db.add(m)
    db.flush()
    v = MappingVersion(
        mapping_id=m.id, version_number=1, status="published", published_by="test",
        schema_snapshot={"source": source_schema, "target": target_schema}, edges_snapshot=[],
    )
    db.add(v)
    db.flush()
    db.add(FieldMapping(
        mapping_id=m.id, version_id=v.id, target_table="company_segments", target_column="id",
        target_is_pk=1, sources=[{"table": "companies", "column": "id"}],
        transformation={"kind": "direct"}, origin="manual",
    ))
    db.add(FieldMapping(
        mapping_id=m.id, version_id=v.id, target_table="company_segments", target_column="segment",
        target_is_pk=0, sources=[{"table": "companies", "column": "annual_revenue"}],
        transformation={
            "kind": "case", "operator": ">", "compare_value": 500_000_000,
            "then_value": "Enterprise", "else_value": "SMB",
        },
        origin="manual",
    ))
    m.current_version_id = v.id
    db.commit()

    p, run = _create_pipeline_and_run(db, admin, (src, tgt), m)
    result = pe.PipelineExecutor.execute(p.id, run.id, trigger="manual")

    assert result["status"] == "completed", result
    conn = sqlite3.connect(tgt_path)
    rows = conn.execute("SELECT id, segment FROM company_segments ORDER BY id").fetchall()
    conn.close()
    # id=1: 800M > 500M -> Enterprise. id=2: 50M is not > 500M -> SMB.
    # id=3: NULL revenue -> SMB (NULL is never "true" in a comparison,
    # matching real CASE WHEN NULL > x THEN ... ELSE ... END semantics).
    assert rows == [(1, "Enterprise"), (2, "SMB"), (3, "SMB")]


def test_load_strategy_full_refresh_override_deletes_stale_rows(
    db, admin, patched_executor_env, physical_sqlite_connections, seeded_mapping_with_field_mappings,
):
    """The target's natural key (cust_id) would make the *default* (None)
    strategy upsert, which never removes a target row whose source row
    was deleted. An explicit load_strategy="full_refresh" must override
    that inference and actually drop stale rows on every run."""
    mapping, _version = seeded_mapping_with_field_mappings
    p, run1 = _create_pipeline_and_run(db, admin, physical_sqlite_connections, mapping)
    pe.PipelineExecutor.execute(p.id, run1.id, trigger="manual")

    src, tgt = physical_sqlite_connections
    conn = sqlite3.connect(src.config["path"])
    conn.execute("DELETE FROM users WHERE id = 3")
    conn.commit()
    conn.close()

    p.load_strategy = "full_refresh"
    db.commit()
    run2 = PipelineRun(pipeline_id=p.id, status="pending", trigger="rerun", parent_run_id=run1.id)
    db.add(run2)
    db.commit()
    db.refresh(run2)
    result = pe.PipelineExecutor.execute(p.id, run2.id, trigger="rerun")

    assert result["status"] == "completed"
    conn = sqlite3.connect(tgt.config["path"])
    rows = conn.execute("SELECT cust_id FROM customers ORDER BY cust_id").fetchall()
    conn.close()
    assert rows == [(1,), (2,)]  # cust_id=3 dropped, not left stale by an upsert


def test_load_strategy_upsert_without_natural_key_raises(
    db, admin, patched_executor_env, physical_sqlite_connections, seeded_mapping_with_field_mappings,
):
    """load_strategy="upsert" must fail clearly, not silently fall back to
    full-refresh, when no field mapping is flagged as the target's PK."""
    from app.models.mapping import FieldMapping

    mapping, version = seeded_mapping_with_field_mappings
    pk_edge = db.query(FieldMapping).filter(
        FieldMapping.version_id == version.id, FieldMapping.target_column == "cust_id",
    ).first()
    pk_edge.target_is_pk = 0
    db.commit()

    p, run = _create_pipeline_and_run(db, admin, physical_sqlite_connections, mapping, load_strategy="upsert")
    result = pe.PipelineExecutor.execute(p.id, run.id, trigger="manual")

    assert result["status"] == "failed"
    assert "upsert" in result["error"].lower()
    assert "primary" in result["error"].lower() or "natural key" in result["error"].lower()
    db.refresh(run)
    assert run.status == "failed"


def test_load_strategy_append_never_deletes_or_updates(
    db, admin, patched_executor_env, tmp_path,
):
    """load_strategy="append" must insert every row on every run and never
    delete or upsert — for fact tables where every row is a new event."""
    import sqlite3 as _sqlite3

    from app.models.connection import DBConnection
    from app.models.mapping import FieldMapping, Mapping, MappingVersion
    from app.connectors.sqlite import SQLiteConnector

    src_path = str(tmp_path / "append_src.db")
    tgt_path = str(tmp_path / "append_tgt.db")

    conn = _sqlite3.connect(src_path)
    conn.execute("CREATE TABLE events (event_id INTEGER, payload TEXT)")
    conn.executemany("INSERT INTO events (event_id, payload) VALUES (?, ?)", [(1, "a"), (2, "b")])
    conn.commit()
    conn.close()

    conn = _sqlite3.connect(tgt_path)
    conn.execute("CREATE TABLE fact_events (event_id INTEGER, payload TEXT)")  # no PK/unique constraint
    conn.commit()
    conn.close()

    src = DBConnection(name="AppendSrc", type="sqlite", config={"path": src_path})
    tgt = DBConnection(name="AppendTgt", type="sqlite", config={"path": tgt_path})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)

    src_connector = SQLiteConnector(src.config["path"])
    tgt_connector = SQLiteConnector(tgt.config["path"])
    source_schema = {t: src_connector.get_table_schema(t) for t in src_connector.get_tables()}
    target_schema = {t: tgt_connector.get_table_schema(t) for t in tgt_connector.get_tables()}
    src_connector.close()
    tgt_connector.close()

    m = Mapping(name="Append Map", source_id=src.id, target_id=tgt.id, status="published", created_by="test")
    db.add(m)
    db.flush()
    v = MappingVersion(
        mapping_id=m.id, version_number=1, status="published", published_by="test",
        schema_snapshot={"source": source_schema, "target": target_schema}, edges_snapshot=[],
    )
    db.add(v)
    db.flush()
    for source_col, target_col in (("event_id", "event_id"), ("payload", "payload")):
        db.add(FieldMapping(
            mapping_id=m.id, version_id=v.id, target_table="fact_events", target_column=target_col,
            target_is_pk=0, sources=[{"table": "events", "column": source_col, "type": "TEXT"}],
            transformation={"kind": "direct"}, origin="manual",
        ))
    m.current_version_id = v.id
    db.commit()
    db.refresh(m)

    p = PipelineCRUD.create_pipeline(
        db, name="Append Test", source_connection_id=src.id, target_connection_id=tgt.id,
        mapping_id=m.id, actor=admin.email, load_strategy="append",
    )
    for trigger in ("manual", "rerun"):
        run = PipelineRun(pipeline_id=p.id, status="pending", trigger=trigger)
        db.add(run)
        db.commit()
        db.refresh(run)
        result = pe.PipelineExecutor.execute(p.id, run.id, trigger=trigger)
        assert result["status"] == "completed"

    conn = _sqlite3.connect(tgt_path)
    count = conn.execute("SELECT COUNT(*) FROM fact_events").fetchone()[0]
    conn.close()
    assert count == 4  # 2 rows x 2 runs, nothing deleted or deduplicated
