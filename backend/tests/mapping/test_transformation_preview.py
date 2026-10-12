"""Tests for GET /api/v1/mappings/{id}/edges/{id}/preview (Enterprise v2,
E10-7 — Schema Mapping Workbench transformation preview)."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app
from app.models.connection import DBConnection
from app.models.mapping import FieldMapping, Mapping


@pytest.fixture()
def client_admin(db, admin):
    def _override_current_user():
        return admin
    app.dependency_overrides[get_current_user] = _override_current_user

    def _get_db_override():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[db_module.get_db] = _get_db_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def physical_source(db, tmp_path):
    path = str(tmp_path / "preview_src.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    conn.execute("INSERT INTO users VALUES (1, '  alice  ', 'alice@x.com')")
    conn.execute("INSERT INTO users VALUES (2, 'Bob', NULL)")
    conn.commit()
    conn.close()

    src = DBConnection(name="PreviewSrc", type="sqlite", config={"path": path})
    tgt = DBConnection(name="PreviewTgt", type="sqlite", config={"path": str(tmp_path / "preview_tgt.db")})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)
    return src, tgt


def _make_mapping_with_edge(db, src, tgt, transformation):
    m = Mapping(name="Preview Map", source_id=src.id, target_id=tgt.id,
                status="draft", created_by="test")
    db.add(m)
    db.flush()
    edge = FieldMapping(
        mapping_id=m.id, target_table="customers", target_column="full_name",
        sources=[{"table": "users", "column": "name", "type": "TEXT"}],
        transformation=transformation, origin="manual",
    )
    db.add(edge)
    db.commit()
    db.refresh(m)
    db.refresh(edge)
    return m, edge


class TestTransformationPreview:
    def test_anonymous_returns_401(self, db, physical_source):
        src, tgt = physical_source

        def _get_db_override():
            try:
                yield db
            finally:
                pass
        app.dependency_overrides[db_module.get_db] = _get_db_override
        try:
            m, edge = _make_mapping_with_edge(db, src, tgt, {"kind": "direct"})
            resp = TestClient(app).get(f"/api/v1/mappings/{m.id}/edges/{edge.id}/preview")
            assert resp.status_code == 401
        finally:
            app.dependency_overrides.clear()

    def test_unknown_edge_returns_404(self, client_admin, db, physical_source):
        src, tgt = physical_source
        m, _edge = _make_mapping_with_edge(db, src, tgt, {"kind": "direct"})
        resp = client_admin.get(f"/api/v1/mappings/{m.id}/edges/999999/preview")
        assert resp.status_code == 404

    def test_trim_transformation_previews_real_sample_values(self, client_admin, db, physical_source):
        src, tgt = physical_source
        m, edge = _make_mapping_with_edge(db, src, tgt, {"kind": "trim"})
        resp = client_admin.get(f"/api/v1/mappings/{m.id}/edges/{edge.id}/preview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert len(body["rows"]) == 2
        row = next(r for r in body["rows"] if r["source_values"]["name"] == "  alice  ")
        assert row["output"] == "alice"

    def test_upper_transformation_handles_null_gracefully(self, client_admin, db, physical_source):
        src, tgt = physical_source
        m, edge = _make_mapping_with_edge(db, src, tgt, {"kind": "upper"})
        resp = client_admin.get(f"/api/v1/mappings/{m.id}/edges/{edge.id}/preview")
        body = resp.json()
        row = next(r for r in body["rows"] if r["source_values"]["name"] == "Bob")
        assert row["output"] == "BOB"

    def test_default_transformation_substitutes_fallback_value(self, client_admin, db, physical_source):
        src, tgt = physical_source
        m = Mapping(name="Default Map", source_id=src.id, target_id=tgt.id, status="draft", created_by="test")
        db.add(m)
        db.flush()
        edge = FieldMapping(
            mapping_id=m.id, target_table="customers", target_column="contact_email",
            sources=[{"table": "users", "column": "email", "type": "TEXT"}],
            transformation={"kind": "default", "value_kind": "literal", "value": "unknown@example.com"},
            origin="manual",
        )
        db.add(edge)
        db.commit()
        db.refresh(edge)

        resp = client_admin.get(f"/api/v1/mappings/{m.id}/edges/{edge.id}/preview")
        body = resp.json()
        bob_row = next(r for r in body["rows"] if r["source_values"]["email"] is None)
        assert bob_row["output"] == "unknown@example.com"
        alice_row = next(r for r in body["rows"] if r["source_values"]["email"] == "alice@x.com")
        assert alice_row["output"] == "alice@x.com"

    def test_lookup_transformation_is_honestly_unavailable(self, client_admin, db, physical_source):
        src, tgt = physical_source
        m, edge = _make_mapping_with_edge(
            db, src, tgt,
            {"kind": "lookup", "table": "regions", "key_column": "code", "value_column": "name"},
        )
        resp = client_admin.get(f"/api/v1/mappings/{m.id}/edges/{edge.id}/preview")
        body = resp.json()
        assert body["available"] is False
        assert "lookup" in body["reason"].lower()
        assert body["rows"] == []

    def test_unreachable_source_connection_is_honestly_unavailable_not_500(self, client_admin, db, physical_source):
        src, tgt = physical_source
        src.config = {"path": "/nonexistent/does-not-exist.db"}
        db.commit()
        m, edge = _make_mapping_with_edge(db, src, tgt, {"kind": "direct"})
        resp = client_admin.get(f"/api/v1/mappings/{m.id}/edges/{edge.id}/preview")
        assert resp.status_code == 200
        assert resp.json()["available"] is False

    def test_case_transformation_previews_the_threshold_conditional(self, client_admin, db, physical_source):
        """id=1 and id=2 stand in for a numeric business column (e.g.
        annual_revenue) — id > 1 -> "high" else "low"."""
        src, tgt = physical_source
        m = Mapping(name="Case Map", source_id=src.id, target_id=tgt.id, status="draft", created_by="test")
        db.add(m)
        db.flush()
        edge = FieldMapping(
            mapping_id=m.id, target_table="customers", target_column="tier",
            sources=[{"table": "users", "column": "id", "type": "INTEGER"}],
            transformation={"kind": "case", "operator": ">", "compare_value": 1,
                            "then_value": "high", "else_value": "low"},
            origin="manual",
        )
        db.add(edge)
        db.commit()
        db.refresh(edge)

        resp = client_admin.get(f"/api/v1/mappings/{m.id}/edges/{edge.id}/preview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        row1 = next(r for r in body["rows"] if r["source_values"]["id"] == 1)
        row2 = next(r for r in body["rows"] if r["source_values"]["id"] == 2)
        assert row1["output"] == "low"   # 1 is not > 1
        assert row2["output"] == "high"  # 2 > 1

    def test_slow_source_query_times_out_instead_of_hanging(self, client_admin, db, physical_source, monkeypatch):
        """build-validation B-E10-12: a slow/unresponsive source must not
        hang the request indefinitely — it degrades to an honest
        'unavailable' with a timeout reason, like every other connector
        failure mode here."""
        import time

        from app.services import transformation_preview_service as tps

        monkeypatch.setattr(tps.settings, "CONNECTOR_TEST_TIMEOUT_SECONDS", 0.2)

        class _SlowConnector:
            def execute_query(self, _sql):
                time.sleep(2)
                return []

            def close(self):
                pass

        monkeypatch.setattr(tps, "get_connector", lambda _conn: _SlowConnector())

        src, tgt = physical_source
        m, edge = _make_mapping_with_edge(db, src, tgt, {"kind": "direct"})
        started = time.monotonic()
        resp = client_admin.get(f"/api/v1/mappings/{m.id}/edges/{edge.id}/preview")
        elapsed = time.monotonic() - started

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert "timed out" in body["reason"].lower()
        assert body["rows"] == []
        assert elapsed < 1.5  # bounded by the 0.2s timeout, not the 2s sleep


# ── apply_transformation_preview: case, direct unit tests ────────────────
# The pure-function evaluator apply_transformation_preview has no direct
# unit tests elsewhere in this file (every other kind is only exercised
# through the full API+DB fixture above) — worth adding here for "case"
# specifically since its NULL/type-mismatch edge cases are easiest to
# pin down without standing up a physical SQLite fixture for each one.

from app.services.transformation_grammar import parse as _parse_transformation
from app.services.transformation_preview_service import apply_transformation_preview


def _case_ast(**overrides):
    payload = {"operator": ">", "compare_value": 500_000_000, "then_value": "Enterprise", "else_value": "SMB"}
    payload.update(overrides)
    return _parse_transformation({"kind": "case", **payload})


def test_apply_case_preview_matches_then_branch():
    ast = _case_ast()
    assert apply_transformation_preview(ast, {"annual_revenue": 800_000_000}) == "Enterprise"


def test_apply_case_preview_matches_else_branch():
    ast = _case_ast()
    assert apply_transformation_preview(ast, {"annual_revenue": 50_000_000}) == "SMB"


def test_apply_case_preview_null_source_takes_else_branch():
    # NULL is never "true" in a SQL comparison — CASE WHEN NULL > x THEN
    # ... ELSE ... END takes ELSE, and the preview must match that exactly.
    ast = _case_ast()
    assert apply_transformation_preview(ast, {"annual_revenue": None}) == "SMB"


@pytest.mark.parametrize("op,left,right,expected", [
    (">", 5, 3, True), (">", 3, 5, False),
    (">=", 5, 5, True), (">=", 4, 5, False),
    ("<", 3, 5, True), ("<", 5, 3, False),
    ("<=", 5, 5, True), ("<=", 6, 5, False),
    ("==", 5, 5, True), ("==", 5, 6, False),
    ("!=", 5, 6, True), ("!=", 5, 5, False),
])
def test_apply_case_preview_every_operator(op, left, right, expected):
    ast = _case_ast(operator=op, compare_value=right, then_value="THEN", else_value="ELSE")
    assert apply_transformation_preview(ast, {"x": left}) == ("THEN" if expected else "ELSE")


def test_apply_case_preview_numeric_then_else_values():
    # employee_count > 0 -> 1 else 0
    ast = _case_ast(operator=">", compare_value=0, then_value=1, else_value=0)
    assert apply_transformation_preview(ast, {"employee_count": 5}) == 1
    assert apply_transformation_preview(ast, {"employee_count": 0}) == 0


def test_apply_case_preview_incomparable_types_are_honestly_unavailable():
    # A string source value can't be compared to a numeric threshold —
    # honest None (this sample doesn't preview), not a crash.
    ast = _case_ast(compare_value=500_000_000)
    assert apply_transformation_preview(ast, {"annual_revenue": "not-a-number"}) is None
