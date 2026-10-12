"""HTTP surface of /api/v1/connectors (tasks #1/#3/#4/#7 wiring)."""


def _create_sqlite(client, name, path):
    return client.post("/api/v1/connectors/",
                       json={"name": name, "type": "sqlite",
                             "config": {"path": path}})


# ── types catalog (task #3, FR1) ─────────────────────────────────

def test_types_lists_all(client_admin):
    r = client_admin.get("/api/v1/connectors/types")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"postgres", "mysql", "oracle", "sqlite", "jdbc"}
    pg = body["postgres"]
    assert pg["category"] == "relational"
    assert any(f["key"] == "password" and f["secret"] for f in pg["fields"])


def test_types_single_and_unknown(client_admin):
    assert client_admin.get("/api/v1/connectors/types/sqlite").status_code == 200
    assert client_admin.get("/api/v1/connectors/types/fake").status_code == 404


# ── create + validation (tasks #1/#3, FR2) ───────────────────────

def test_create_and_get_roundtrip(client_admin, sqlite_file):
    r = _create_sqlite(client_admin, "api-conn", sqlite_file)
    assert r.status_code == 201
    body = r.json()
    assert body["health_status"] == "unknown"
    assert body["is_deleted"] is False
    assert body["created_by"] == "admin@test.local"

    r2 = client_admin.get(f"/api/v1/connectors/{body['id']}")
    assert r2.status_code == 200
    assert r2.json()["name"] == "api-conn"


def test_create_unknown_type_422(client_admin):
    r = client_admin.post("/api/v1/connectors/",
                          json={"name": "x", "type": "mongodb", "config": {}})
    assert r.status_code == 422


def test_create_missing_required_field_422(client_admin):
    r = client_admin.post("/api/v1/connectors/",
                          json={"name": "x", "type": "postgres",
                                "config": {"host": "h"}})
    assert r.status_code == 422


def test_create_duplicate_409(client_admin, sqlite_file):
    assert _create_sqlite(client_admin, "dup-api", sqlite_file).status_code == 201
    assert _create_sqlite(client_admin, "dup-api", sqlite_file).status_code == 409


def test_create_strips_unknown_config_keys(client_admin, sqlite_file):
    r = client_admin.post("/api/v1/connectors/",
                          json={"name": "stripme", "type": "sqlite",
                                "config": {"path": sqlite_file, "extra": "x"}})
    assert r.status_code == 201
    assert "extra" not in r.json()["config"]


def test_connection_environment_label_defaults_and_updates(client_admin, sqlite_file):
    created = _create_sqlite(client_admin, "env-api", sqlite_file)
    assert created.status_code == 201
    assert created.json()["environment"] == "dev"

    updated = client_admin.patch(
        f"/api/v1/connectors/{created.json()['id']}/environment",
        json={"environment": "prod"},
    )
    assert updated.status_code == 200
    assert updated.json()["environment"] == "prod"
    assert client_admin.patch(
        f"/api/v1/connectors/{created.json()['id']}/environment",
        json={"environment": "production"},
    ).status_code == 422


# ── secret redaction (FR3) ───────────────────────────────────────

def test_password_never_returned(client_admin):
    r = client_admin.post("/api/v1/connectors/", json={
        "name": "pg-secret", "type": "postgres",
        "config": {"host": "h", "port": 5432, "dbname": "d",
                   "user": "u", "password": "hunter2"},
    })
    assert r.status_code == 201
    assert "hunter2" not in r.text
    assert r.json()["config"]["password"] == "***"

    list_resp = client_admin.get("/api/v1/connectors/")
    assert "hunter2" not in list_resp.text

    get_resp = client_admin.get(f"/api/v1/connectors/{r.json()['id']}")
    assert "hunter2" not in get_resp.text


# ── test connection (task #4, FR4) ───────────────────────────────

def test_test_connection_success_diagnostics(client_admin, sqlite_conn):
    r = client_admin.post(f"/api/v1/connectors/{sqlite_conn.id}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "connected"
    assert body["error"] is None
    d = body["diagnostics"]
    assert d["reachable"] and d["authenticated"] and d["database_accessible"]
    assert d["version"].startswith("SQLite")
    assert d["latency_ms"] is not None


def test_test_connection_failure_diagnostics_and_health(client_admin, db):
    r = _create_sqlite(client_admin, "missing-file", "/nonexistent/nope.db")
    cid = r.json()["id"]
    t = client_admin.post(f"/api/v1/connectors/{cid}/test")
    assert t.status_code == 200
    body = t.json()
    assert body["status"] == "failed"
    assert body["diagnostics"]["reachable"] is False
    assert body["error"]["code"] == "CONNECTION_REFUSED"
    assert "not found" in body["error"]["message"]

    # health persisted (FR5 write path)
    g = client_admin.get(f"/api/v1/connectors/{cid}")
    assert g.json()["health_status"] == "down"
    assert g.json()["last_test_error"]


def test_test_connection_emits_audit(client_admin, sqlite_conn, db):
    client_admin.post(f"/api/v1/connectors/{sqlite_conn.id}/test")
    from app.models.audit import AuditLog
    row = db.query(AuditLog).filter(
        AuditLog.event_type == "connector_tested").one()
    assert row.connection_id == sqlite_conn.id
    assert row.payload["status"] == "connected"


# ── health summary (task #5, FR5) ────────────────────────────────

def test_health_summary_endpoint(client_admin, sqlite_conn):
    client_admin.post(f"/api/v1/connectors/{sqlite_conn.id}/test")
    r = client_admin.get("/api/v1/connectors/health-summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["healthy"] == 1


# ── soft delete / restore / hard delete (task #7, FR7) ───────────

def test_delete_without_dependents_soft_deletes(client_admin, sqlite_conn):
    r = client_admin.delete(f"/api/v1/connectors/{sqlite_conn.id}")
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"
    assert client_admin.get(f"/api/v1/connectors/{sqlite_conn.id}").status_code == 404
    assert client_admin.get("/api/v1/connectors/").json() == []


def test_delete_with_dependents_requires_confirm(client_admin, db, sqlite_file):
    src = _create_sqlite(client_admin, "wired-src", sqlite_file).json()
    tgt = _create_sqlite(client_admin, "wired-tgt", sqlite_file + "2").json()

    from app.models.mapping import Mapping
    db.add(Mapping(name="M", source_id=src["id"], target_id=tgt["id"],
                   status="draft", created_by="t"))
    db.commit()

    warn = client_admin.delete(f"/api/v1/connectors/{src['id']}")
    assert warn.status_code == 200
    assert warn.json()["requires_confirm"] is True
    # not deleted yet
    assert client_admin.get(f"/api/v1/connectors/{src['id']}").status_code == 200

    done = client_admin.delete(f"/api/v1/connectors/{src['id']}?confirm=true")
    assert done.json()["status"] == "deleted"
    assert done.json()["affected_dependents"] == 1


def test_restore_endpoint(client_admin, sqlite_conn):
    client_admin.delete(f"/api/v1/connectors/{sqlite_conn.id}")
    r = client_admin.post(f"/api/v1/connectors/{sqlite_conn.id}/restore")
    assert r.status_code == 200
    assert r.json()["is_deleted"] is False


# NOTE: client_admin and client_analyst override the same app instance, so a
# single test must use only one of them — split per role.

def test_deleted_list_as_admin(client_admin, sqlite_conn):
    client_admin.delete(f"/api/v1/connectors/{sqlite_conn.id}")
    r = client_admin.get("/api/v1/connectors/deleted")
    assert r.status_code == 200
    assert [c["name"] for c in r.json()] == ["test-sqlite"]


def test_deleted_list_forbidden_for_analyst(client_analyst):
    assert client_analyst.get("/api/v1/connectors/deleted").status_code == 403


def test_hard_delete_admin_only(client_analyst, sqlite_conn):
    r = client_analyst.delete(f"/api/v1/connectors/{sqlite_conn.id}/hard")
    assert r.status_code == 403


def test_hard_delete_as_admin(client_admin, sqlite_conn):
    r = client_admin.delete(f"/api/v1/connectors/{sqlite_conn.id}/hard")
    assert r.status_code == 204


# ── per-owner isolation (tenant_isolation_tasks slice #1) ────────

def test_one_user_does_not_see_another_users_connection(client_factory, analyst, analyst2, sqlite_file):
    mine = client_factory(analyst)
    created = _create_sqlite(mine, "iso-conn", sqlite_file)
    assert created.status_code == 201
    conn_id = created.json()["id"]
    assert [c["name"] for c in mine.get("/api/v1/connectors/").json()] == ["iso-conn"]

    theirs = client_factory(analyst2)
    assert theirs.get("/api/v1/connectors/").json() == []
    assert theirs.get(f"/api/v1/connectors/{conn_id}").status_code == 404
    assert theirs.post(f"/api/v1/connectors/{conn_id}/test").status_code == 404
    assert theirs.delete(f"/api/v1/connectors/{conn_id}").status_code == 404


def test_different_owners_can_reuse_the_same_connection_name(client_factory, analyst, analyst2, sqlite_file):
    # client_factory swaps a single global dependency-override slot, so a
    # second client() call redirects EVERY client's next request (including
    # ones made through an earlier handle) — each user's calls must fully
    # finish before switching identities.
    assert _create_sqlite(client_factory(analyst), "shared-name", sqlite_file).status_code == 201
    assert _create_sqlite(client_factory(analyst2), "shared-name", sqlite_file).status_code == 201


def test_admin_sees_and_can_act_on_every_owners_connection(client_factory, admin, analyst, sqlite_file):
    mine = client_factory(analyst)
    conn_id = _create_sqlite(mine, "admin-visible", sqlite_file).json()["id"]

    as_admin = client_factory(admin)
    assert as_admin.get(f"/api/v1/connectors/{conn_id}").status_code == 200
    names = [c["name"] for c in as_admin.get("/api/v1/connectors/").json()]
    assert "admin-visible" in names
