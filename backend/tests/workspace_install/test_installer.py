"""Tests for the Databricks workspace installer (service + API) using a scripted fake SDK client."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from databricks.sdk.errors import NotFound, PermissionDenied, ResourceAlreadyExists

from app.api.routers.workspace_install import get_workspace_client
from app.core.config import settings
from app.main import app
from app.models.workspace_install import WorkspaceInstall
from app.services import databricks_installer_service as svc
from app.services.databricks_installer_service import (
    InstallerError, InstallOptions, WorkspaceInstaller,
)

HOST = "https://dbc-test.cloud.databricks.com"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "DQ_INGEST_KEY", "ingest-secret")
    monkeypatch.setattr(settings, "BACKEND_URL", "https://dataone.example.com")
    monkeypatch.setattr(settings, "DATABRICKS_WORKSPACE_URL", HOST)


def fake_client(*, existing=False, existing_job=None):
    """Fake WorkspaceClient. existing=True -> everything already exists (re-run)."""
    w = MagicMock()
    w.config.host = HOST
    w.current_user.me.return_value = SimpleNamespace(user_name="me@corp.com")
    w.catalogs.get.return_value = SimpleNamespace(name="main")
    missing = NotFound("nope")
    for api, getter in ((w.schemas, "get"), (w.volumes, "read"), (w.workspace, "get_status")):
        m = getattr(api, getter)
        if existing:
            m.return_value = object()
        else:
            m.side_effect = missing
    w.secrets.create_scope.side_effect = ResourceAlreadyExists("exists") if existing else None
    w.jobs.list.return_value = iter([SimpleNamespace(job_id=existing_job)] if existing_job else [])
    w.jobs.create.return_value = SimpleNamespace(job_id=111)
    w.jobs.run_now.return_value = SimpleNamespace(run_id=999)
    return w


def statuses(outcome):
    return [(s.step, s.status) for s in outcome.steps]


class TestInstallerService:
    def test_fresh_install_creates_everything_in_the_users_workspace(self):
        w = fake_client()
        out = WorkspaceInstaller(w, "me@corp.com").install(InstallOptions(catalog="main", schema_prefix="acme"))

        assert out.ok and out.job_id == 111
        assert statuses(out) == [
            ("catalog", "unchanged"),
            ("schema", "created"), ("schema", "created"), ("schema", "created"),
            ("volume", "created"), ("notebook", "created"), ("secrets", "created"), ("job", "created"),
        ]
        assert [c.kwargs["name"] for c in w.schemas.create.call_args_list] == ["acme_bronze", "acme_silver", "acme_gold"]
        vol = w.volumes.create.call_args.kwargs
        assert (vol["catalog_name"], vol["schema_name"], vol["name"]) == ("main", "acme_bronze", "acme_landing")

        # notebook uploaded under the signed-in user's home, with the real source
        path, content = w.workspace.upload.call_args.args
        assert path == "/Workspace/Users/me@corp.com/DataOne/dataone_data_quality"
        assert b"DataOne" in content.read()
        assert w.workspace.upload.call_args.kwargs["overwrite"] is True

        # ingest key stored in the user's secret scope
        w.secrets.put_secret.assert_called_once_with("dataone", "dq-ingest-key", string_value="ingest-secret")

        # job: serverless notebook task (no cluster spec), params point at this install
        job = w.jobs.create.call_args.kwargs
        task = job["tasks"][0]
        assert job["name"] == "DataOne Data Quality" and task.new_cluster is None
        assert task.notebook_task.notebook_path == "/Workspace/Users/me@corp.com/DataOne/dataone_data_quality"
        assert task.notebook_task.base_parameters == {
            "catalog": "main", "schemas": "acme_bronze,acme_silver,acme_gold",
            "backend_url": "https://dataone.example.com", "secret_scope": "dataone", "secret_key": "dq-ingest-key",
        }
        assert job["schedule"] is None

    def test_rerun_is_idempotent_and_updates_the_existing_job(self):
        w = fake_client(existing=True, existing_job=42)
        out = WorkspaceInstaller(w, "me@corp.com").install(InstallOptions(catalog="main"))

        assert out.ok and out.job_id == 42
        w.schemas.create.assert_not_called()
        w.volumes.create.assert_not_called()
        w.jobs.create.assert_not_called()
        assert w.jobs.reset.call_args.args[0] == 42
        assert ("notebook", "updated") in statuses(out) and ("secrets", "updated") in statuses(out)
        assert ("job", "updated") in statuses(out)

    def test_schedule_is_applied(self):
        w = fake_client()
        WorkspaceInstaller(w, "me@corp.com").install(InstallOptions(catalog="main", schedule_cron="0 0 6 * * ?"))
        sched = w.jobs.create.call_args.kwargs["schedule"]
        assert sched.quartz_cron_expression == "0 0 6 * * ?" and sched.timezone_id == "UTC"

    def test_notebooks_follow_the_databricks_identity_not_the_dataone_email(self):
        w = fake_client()
        w.current_user.me.return_value = SimpleNamespace(user_name="dbx.person@corp.com")
        out = WorkspaceInstaller(w, "other@dataone.local").install(InstallOptions(catalog="main"))
        assert out.notebook_folder == "/Workspace/Users/dbx.person@corp.com/DataOne"

    def test_rejected_token_stops_before_creating_anything(self):
        w = fake_client()
        w.current_user.me.side_effect = PermissionDenied("invalid token")
        out = WorkspaceInstaller(w, "me@corp.com").install(InstallOptions(catalog="main"))
        assert not out.ok and statuses(out) == [("auth", "failed")]
        w.catalogs.get.assert_not_called()

    def test_missing_catalog_fails_first_and_creates_nothing(self):
        w = fake_client()
        w.catalogs.get.side_effect = NotFound("x")
        out = WorkspaceInstaller(w, "me@corp.com").install(InstallOptions(catalog="ghost"))
        assert not out.ok and statuses(out) == [("catalog", "failed")]
        assert "ghost" in out.steps[0].detail
        w.schemas.create.assert_not_called()

    def test_permission_error_stops_the_run_and_keeps_earlier_steps(self):
        w = fake_client()
        w.volumes.create.side_effect = PermissionDenied("User cannot CREATE VOLUME")
        out = WorkspaceInstaller(w, "me@corp.com").install(InstallOptions(catalog="main"))
        assert not out.ok
        assert statuses(out)[-1] == ("volume", "failed") and "CREATE VOLUME" in out.steps[-1].detail
        w.workspace.upload.assert_not_called()
        w.jobs.create.assert_not_called()
        assert sum(1 for s in out.steps if s.step == "schema" and s.status == "created") == 3

    @pytest.mark.parametrize("bad", [
        {"catalog": "has space"}, {"catalog": "a;drop"}, {"catalog": "ok", "schema_prefix": "x-y"},
        {"catalog": "ok", "secret_scope": "bad scope!"}, {"catalog": ""},
    ])
    def test_input_validation(self, bad):
        with pytest.raises(InstallerError):
            WorkspaceInstaller(fake_client(), "me@corp.com").install(InstallOptions(**bad))

    def test_requires_ingest_key(self, monkeypatch):
        monkeypatch.setattr(settings, "DQ_INGEST_KEY", None)
        with pytest.raises(InstallerError, match="DQ_INGEST_KEY"):
            WorkspaceInstaller(fake_client(), "me@corp.com").install(InstallOptions(catalog="main"))

    def test_refuses_localhost_backend_url(self, monkeypatch):
        monkeypatch.setattr(settings, "BACKEND_URL", "http://localhost:8011")
        with pytest.raises(InstallerError, match="localhost"):
            WorkspaceInstaller(fake_client(), "me@corp.com").install(InstallOptions(catalog="main"))

    def test_notebook_asset_ships_with_the_server(self):
        for filename in svc.NOTEBOOKS.values():
            assert (svc.ASSETS_DIR / filename).is_file()


class TestInstallerApi:
    @pytest.fixture()
    def api(self, make_client, admin, db):
        w = fake_client()
        client = make_client(admin)
        app.dependency_overrides[get_workspace_client] = lambda: w
        return client, w, db

    def test_install_then_status_and_run(self, api):
        client, w, db = api
        assert client.get("/api/v1/databricks/install/status").json()["installed"] is None

        resp = client.post("/api/v1/databricks/install", json={"catalog": "main", "schema_prefix": "acme"})
        assert resp.status_code == 200 and resp.json()["ok"] is True and resp.json()["job_id"] == 111

        status = client.get("/api/v1/databricks/install/status").json()
        assert status["installed"]["catalog"] == "main" and status["installed"]["job_id"] == 111
        assert status["up_to_date"] is True and status["current_release"] == svc.RELEASE

        run = client.post("/api/v1/databricks/install/run").json()
        assert run["run_id"] == 999 and run["run_url"] == f"{HOST}/jobs/111/runs/999"

    def test_signed_in_flag_counts_the_apps_proxy_token(self, api, admin, db):
        client, *_ = api
        admin.databricks_access_token = None
        db.commit()
        assert client.get("/api/v1/databricks/install/status").json()["signed_in_with_databricks"] is False
        hdr = {"X-Forwarded-Access-Token": "t"}
        assert client.get("/api/v1/databricks/install/status", headers=hdr).json()["signed_in_with_databricks"] is True

    def test_outdated_release_is_flagged(self, api):
        client, w, db = api
        client.post("/api/v1/databricks/install", json={"catalog": "main"})
        row = db.query(WorkspaceInstall).one()
        row.release = "0.0.1"
        db.commit()
        status = client.get("/api/v1/databricks/install/status").json()
        assert status["up_to_date"] is False

    def test_reinstall_updates_one_row(self, api):
        client, w, db = api
        client.post("/api/v1/databricks/install", json={"catalog": "main"})
        client.post("/api/v1/databricks/install", json={"catalog": "other"})
        rows = db.query(WorkspaceInstall).all()
        assert len(rows) == 1 and rows[0].catalog == "other"

    def test_failed_install_is_reported_not_recorded(self, api):
        client, w, db = api
        w.catalogs.get.side_effect = NotFound("x")
        body = client.post("/api/v1/databricks/install", json={"catalog": "ghost"}).json()
        assert body["ok"] is False and body["steps"][0]["status"] == "failed"
        assert db.query(WorkspaceInstall).count() == 0

    def test_bad_input_is_a_400(self, api):
        client, *_ = api
        assert client.post("/api/v1/databricks/install", json={"catalog": "bad name"}).status_code == 400

    def test_run_requires_install(self, api):
        client, *_ = api
        assert client.post("/api/v1/databricks/install/run").status_code == 409

    def test_catalog_list_only_offers_managed_catalogs(self, api):
        client, w, _ = api
        mk = lambda n, t: SimpleNamespace(name=n, catalog_type=SimpleNamespace(value=t))
        w.catalogs.list.return_value = iter([mk("main", "MANAGED_CATALOG"), mk("system", "SYSTEM_CATALOG"),
                                             mk("pg", "FOREIGN_CATALOG")])
        assert client.get("/api/v1/databricks/install/catalogs").json() == {"catalogs": ["main"]}

    def test_viewer_is_forbidden(self, make_client, viewer):
        assert make_client(viewer).post("/api/v1/databricks/install", json={"catalog": "main"}).status_code == 403

    def test_anonymous_is_unauthorized(self, make_client):
        assert make_client(None).get("/api/v1/databricks/install/status").status_code == 401


class TestClientFactory:
    """get_workspace_client builds the client from the signed-in user's own token."""

    @pytest.fixture()
    def captured(self, monkeypatch):
        calls = []
        monkeypatch.setattr("app.api.routers.workspace_install.WorkspaceClient",
                            lambda **kw: calls.append(kw) or SimpleNamespace(**kw))
        return calls

    def test_prefers_fresh_apps_proxy_token_then_stored_token(self, admin, db, captured):
        admin.databricks_access_token = "stored-token"
        get_workspace_client(user=admin, db=db, x_forwarded_access_token="proxy-token")
        get_workspace_client(user=admin, db=db, x_forwarded_access_token=None)
        assert [c["token"] for c in captured] == ["proxy-token", "stored-token"]
        assert all(c["host"] == HOST and c["auth_type"] == "pat" for c in captured)

    def test_needs_databricks_sign_in(self, admin, db, captured):
        from fastapi import HTTPException
        admin.databricks_access_token = None
        with pytest.raises(HTTPException) as e:
            get_workspace_client(user=admin, db=db, x_forwarded_access_token=None)
        assert e.value.status_code == 401 and not captured

    def test_needs_a_configured_workspace(self, admin, db, captured, monkeypatch):
        from fastapi import HTTPException
        monkeypatch.setattr(settings, "DATABRICKS_WORKSPACE_URL", None)
        monkeypatch.setattr(settings, "DATABRICKS_HOST", None)
        with pytest.raises(HTTPException) as e:
            get_workspace_client(user=admin, db=db, x_forwarded_access_token="t")
        assert e.value.status_code == 409

    def test_expired_stored_token_that_cannot_refresh_asks_to_sign_in_again(self, admin, db, captured):
        from datetime import datetime, timedelta, timezone
        from fastapi import HTTPException
        admin.databricks_access_token = "old"
        admin.databricks_token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        with pytest.raises(HTTPException) as e:
            get_workspace_client(user=admin, db=db, x_forwarded_access_token=None)
        assert e.value.status_code == 401 and "sign in" in e.value.detail.lower()
