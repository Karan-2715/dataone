"""Schema Mapper catalogs for Databricks connections: live Unity Catalog scan, TTL cache, no foreign snapshots."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from app.core.config import settings
from app.models.connection import DBConnection
from app.models.schema_snapshot import SchemaSnapshot
from app.services import databricks_schema_scan as scan_mod

SCAN = {
    "workspace.default.titanic": [{"name": "Age", "type": "string", "nullable": True, "primary_key": False,
                                   "foreign_keys": [], "ordinal_position": 1, "default": None}],
    "workspace.dataone_dq.(empty_schema)": [],
}


class _FakeWC:
    """Just enough of WorkspaceClient for scan_unity_catalog."""

    def __init__(self):
        col = lambda n, p, t="string": NS(name=n, type_text=t, type_name=None, nullable=True, position=p)  # noqa: E731
        self.catalogs = NS(list=lambda: [NS(name="system"), NS(name="workspace"), NS(name="broken")])

        def schemas_list(catalog_name):
            if catalog_name == "broken":
                raise RuntimeError("no access")
            return [NS(name="information_schema"), NS(name="default"), NS(name="empty")]

        def tables_list(catalog_name, schema_name):
            if schema_name == "empty":
                return []
            return [NS(name="titanic", columns=[col("Name", 2), col("Age", 1, "int")])]

        self.schemas = NS(list=schemas_list)
        self.tables = NS(list=tables_list)


def test_scan_lists_catalogs_schemas_tables_and_columns():
    out = scan_mod.scan_unity_catalog(_FakeWC())
    assert set(out) == {"workspace.default.titanic", "workspace.empty.(empty_schema)"}  # system/information_schema/broken skipped
    cols = out["workspace.default.titanic"]
    assert [c["name"] for c in cols] == ["Age", "Name"]  # ordered by position
    assert cols[0]["type"] == "int" and cols[0]["ordinal_position"] == 1 and cols[0]["primary_key"] is False


def _databricks_conn(db, owner="u@x.com", name="Databricks — u"):
    c = DBConnection(name=name, type="databricks", config={"host": "h"}, owner_email=owner, is_deleted=False)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _patch_scan(monkeypatch, result=SCAN, calls=None):
    import app.services.databricks_ingestion_service as ing

    monkeypatch.setattr(ing, "_get_workspace_client", lambda token=None: object())

    def fake(wc):
        if calls is not None:
            calls.append(1)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(scan_mod, "scan_unity_catalog", fake)


def test_schema_comes_from_live_scan_not_another_connections_snapshot(client_admin, db, monkeypatch):
    foreign = _databricks_conn(db, owner="other@x.com", name="Databricks — other")
    db.add(SchemaSnapshot(connection_id=foreign.id, schema_json={"old_ws_catalog.s.t": []}))
    mine = _databricks_conn(db)
    db.commit()
    _patch_scan(monkeypatch)

    body = client_admin.get(f"/api/v1/connectors/{mine.id}/schema").json()
    assert body["schema"] == SCAN  # live, current workspace — not the foreign "old_ws_catalog"
    snap = db.query(SchemaSnapshot).filter_by(connection_id=mine.id).one()
    assert snap.schema_json == SCAN and snap.schema_hash


def test_fresh_snapshot_is_served_without_rescanning(client_admin, db, monkeypatch):
    mine = _databricks_conn(db)
    calls = []
    _patch_scan(monkeypatch, calls=calls)
    client_admin.get(f"/api/v1/connectors/{mine.id}/schema")
    client_admin.get(f"/api/v1/connectors/{mine.id}/schema")
    assert len(calls) == 1


def test_stale_snapshot_is_rescanned(client_admin, db, monkeypatch):
    mine = _databricks_conn(db)
    db.add(SchemaSnapshot(connection_id=mine.id, schema_json={"stale.s.t": []},
                          captured_at=datetime.utcnow() - timedelta(seconds=settings.DATABRICKS_SCHEMA_TTL_SECONDS + 60)))
    db.commit()
    _patch_scan(monkeypatch)
    assert client_admin.get(f"/api/v1/connectors/{mine.id}/schema").json()["schema"] == SCAN
    assert db.query(SchemaSnapshot).filter_by(connection_id=mine.id).one().schema_json == SCAN  # updated in place


def test_scan_failure_falls_back_to_existing_snapshot(client_admin, db, monkeypatch):
    mine = _databricks_conn(db)
    db.add(SchemaSnapshot(connection_id=mine.id, schema_json={"cached.s.t": []},
                          captured_at=datetime.utcnow() - timedelta(days=2)))
    db.commit()
    _patch_scan(monkeypatch, result=RuntimeError("boom"))
    assert client_admin.get(f"/api/v1/connectors/{mine.id}/schema").json()["schema"] == {"cached.s.t": []}


def test_tz_aware_snapshot_timestamps_do_not_break_the_ttl_check(db, monkeypatch):
    """Postgres hands back tz-aware ``captured_at``; comparing it to a naive 'now' used to 500 every repeat load."""
    from app.api.routers.connectors import _databricks_live_schema

    mine = _databricks_conn(db)
    _patch_scan(monkeypatch)

    def snap(age):
        return NS(captured_at=datetime.now(timezone.utc) - age, schema_json={"a.b.c": []}, schema_hash=None)

    assert _databricks_live_schema(db, mine, snap(timedelta(seconds=5))) == {"a.b.c": []}  # fresh -> served, no rescan
    assert _databricks_live_schema(db, mine, snap(timedelta(days=1))) == SCAN              # stale -> rescanned
