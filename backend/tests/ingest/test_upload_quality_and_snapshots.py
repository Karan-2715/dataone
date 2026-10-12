import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.file_ingest_parser import parse_tabular_file
from app.services.upload_quality import profile_rows


def test_profile_flags_missing_values_and_duplicates():
    cols, rows = parse_tabular_file("a.csv", b"id,age,note\n1,22,\n2,,\n2,,\n3,40,\n")
    q = profile_rows(cols, rows)
    assert q["row_count"] == 4 and q["column_count"] == 3
    assert q["duplicate_rows"] == 1 and q["uniqueness"] == 75.0
    # missing cells: age x2, note x4 = 6 of 12
    assert q["completeness"] == 50.0
    details = {(i["column"], i["check"]) for i in q["issues"]}
    assert ("note", "completeness") in details and ("age", "completeness") in details
    assert (None, "uniqueness") in details


def test_profile_clean_data_has_no_issues():
    cols, rows = parse_tabular_file("a.csv", b"id,name\n1,a\n2,b\n")
    q = profile_rows(cols, rows)
    assert q["completeness"] == 100.0 and q["uniqueness"] == 100.0 and q["issues"] == []


@pytest.fixture()
def db():
    sa = pytest.importorskip("sqlalchemy")
    from app.core.database import Base
    from app.models.connection import DBConnection
    from app.models.schema_snapshot import SchemaSnapshot  # noqa: F401  (registers the table)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DBConnection.__table__, SchemaSnapshot.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_snapshot_refresh_replaces_empty_schema_placeholder(db):
    from app.models.connection import DBConnection
    from app.models.schema_snapshot import SchemaSnapshot
    from app.services.upload_snapshot_refresh import refresh_snapshots_for_uploaded_table

    c1 = DBConnection(name="dbx1", type="databricks", is_deleted=False, owner_email="a@x.com", config={})
    c2 = DBConnection(name="pg", type="postgresql", is_deleted=False, owner_email="a@x.com", config={})
    c3 = DBConnection(name="dbx-nosnap", type="databricks", is_deleted=False, owner_email="a@x.com", config={})
    db.add_all([c1, c2, c3])
    db.flush()
    db.add(SchemaSnapshot(connection_id=c1.id, schema_json={"workspace.default.(empty_schema)": [], "workspace.other.t": []}))
    db.add(SchemaSnapshot(connection_id=c2.id, schema_json={"public.x": []}))
    db.commit()

    updated = refresh_snapshots_for_uploaded_table(db, "workspace", "default", "titanic", ["Age", "Name"])
    db.commit()

    assert updated == 1  # only the databricks connection that has a snapshot
    data = db.query(SchemaSnapshot).filter_by(connection_id=c1.id).one().schema_json
    assert "workspace.default.(empty_schema)" not in data
    assert [c["name"] for c in data["workspace.default.titanic"]] == ["Age", "Name"]
    assert "workspace.other.t" in data
    assert db.query(SchemaSnapshot).filter_by(connection_id=c2.id).one().schema_json == {"public.x": []}


def test_upload_profile_builds_valid_dashboard_payload():
    from app.services.upload_quality import to_dq_run

    cols, rows = parse_tabular_file("t.csv", b"id,age\n1,\n2,\n3,40\n")
    run = to_dq_run("workspace", "default", "t", profile_rows(cols, rows))
    assert run.run_id == "upload:workspace.default.t" and run.source == "upload"
    table = run.tables[0]
    assert table.table_name == "default.t" and table.row_count == 3
    assert table.completeness == 66.7 and table.validity is None and table.freshness == 100.0
    assert table.issues[0].column == "age"
