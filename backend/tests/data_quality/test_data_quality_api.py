"""Tests for the Databricks data-quality ingest + summary APIs."""
import pytest

from app.api.routers import data_quality as data_quality_router
from app.core.config import settings
from app.models.connection import DBConnection
from app.schemas.data_quality import DQRunIn
from app.services import catalog_quality_service

KEY = "test-ingest-key"


@pytest.fixture(autouse=True)
def _ingest_key(monkeypatch):
    monkeypatch.setattr(settings, "DQ_INGEST_KEY", KEY)


def _payload(run_id="run-1", **overrides):
    body = {
        "run_id": run_id,
        "catalog": "workspace",
        "schema_name": "sales",
        "completed_at": "2026-10-09T10:00:00Z",
        "tables": [
            {"table_name": "workspace.sales.orders", "row_count": 100, "column_count": 5,
             "completeness": 90, "uniqueness": 100, "validity": 80, "freshness": 100,
             "issues": [{"column": "email", "check": "completeness", "detail": "12% null"}]},
            {"table_name": "workspace.sales.customers", "row_count": 50, "column_count": 4,
             "completeness": 100, "uniqueness": 90, "validity": None, "freshness": 60},
        ],
    }
    body.update(overrides)
    return body


def _post(client, body, key=KEY):
    headers = {"X-DataOne-Ingest-Key": key} if key is not None else {}
    return client.post("/api/v1/data-quality/ingest", json=body, headers=headers)


class TestIngest:
    def test_ingest_computes_scores_and_summary_reads_them(self, client):
        resp = _post(client, _payload())
        assert resp.status_code == 201
        # orders = mean(90,100,80,100)=92.5 ; customers = mean(100,90,60)=83.3 (validity NULL ignored)
        assert resp.json() == {"run_id": "run-1", "table_count": 2, "overall_score": 87.9, "replaced": False}

        summary = client.get("/api/v1/data-quality/summary").json()
        assert summary["ingest_configured"] is True
        latest = summary["latest"]
        assert latest["overall_score"] == 87.9
        assert latest["completeness"] == 95.0
        assert latest["validity"] == 80.0  # only measured values are averaged
        assert latest["total_rows"] == 150
        # worst table first
        assert [t["table_name"] for t in summary["tables"]] == [
            "workspace.sales.customers", "workspace.sales.orders",
        ]
        assert summary["tables"][1]["issues"][0]["column"] == "email"
        assert summary["tables"][0]["validity"] is None  # never fabricated

    def test_same_run_id_replaces_instead_of_duplicating(self, client):
        assert _post(client, _payload()).status_code == 201
        resp = _post(client, _payload(tables=[_payload()["tables"][0]]))
        assert resp.status_code == 201 and resp.json()["replaced"] is True
        summary = client.get("/api/v1/data-quality/summary").json()
        assert summary["latest"]["table_count"] == 1
        assert len(summary["trend"]) == 1

    def test_trend_is_chronological_and_latest_is_newest(self, client):
        _post(client, _payload("a", completed_at="2026-10-08T10:00:00Z"))
        _post(client, _payload("b", completed_at="2026-10-09T10:00:00Z"))
        summary = client.get("/api/v1/data-quality/summary").json()
        assert summary["latest"]["run_id"] == "b"
        assert [p["completed_at"][:10] for p in summary["trend"]] == ["2026-10-08", "2026-10-09"]

    def test_wrong_key_rejected(self, client):
        assert _post(client, _payload(), key="nope").status_code == 401

    def test_missing_key_rejected(self, client):
        assert _post(client, _payload(), key=None).status_code == 401

    def test_disabled_when_key_unset(self, client, monkeypatch):
        monkeypatch.setattr(settings, "DQ_INGEST_KEY", None)
        assert _post(client, _payload(), key="anything").status_code == 503

    @pytest.mark.parametrize("bad", [
        {"tables": []},
        {"run_id": ""},
        {"tables": [{"table_name": "t", "completeness": 101}]},
        {"tables": [{"table_name": "t", "freshness": -1}]},
    ])
    def test_invalid_payload_422(self, client, bad):
        assert _post(client, _payload(**bad)).status_code == 422


class TestSummary:
    def test_empty_state(self, client):
        data = client.get("/api/v1/data-quality/summary").json()
        assert data["latest"] is None and data["tables"] == [] and data["trend"] == []

    def test_table_limit(self, client):
        _post(client, _payload())
        data = client.get("/api/v1/data-quality/summary?table_limit=1").json()
        assert len(data["tables"]) == 1

    def test_requires_login(self, make_client):
        assert make_client(None).get("/api/v1/data-quality/summary").status_code == 401


class TestCatalogQualityRun:
    def test_profiles_selected_table_and_immediately_updates_summary(self, client, db, admin, monkeypatch):
        db.add(DBConnection(
            name="Databricks", type="databricks", owner_email=admin.email,
            config={"server_hostname": "example", "http_path": "/sql/warehouse", "access_token": "x"},
        ))
        db.commit()
        run = DQRunIn.model_validate(_payload(
            run_id="catalog-quality:test",
            source="databricks-manual:test",
            catalog="dataone_mysql_dataone_source_ba4f108677",
            schema_name="dataone_source",
            tables=[{
                "table_name": "dataone_mysql_dataone_source_ba4f108677.dataone_source.customer_orders",
                "row_count": 10, "column_count": 2, "completeness": 90,
                "uniqueness": 90, "validity": 88.9, "freshness": None,
            }],
        ))
        monkeypatch.setattr(data_quality_router, "profile_catalog_table", lambda *_args: run)

        response = client.post("/api/v1/data-quality/catalog/run", json={
            "catalog": "dataone_mysql_dataone_source_ba4f108677",
            "schema_name": "dataone_source",
            "table": "customer_orders",
        })

        assert response.status_code == 200
        assert response.json()["run_id"] == "catalog-quality:test"
        summary = client.get("/api/v1/data-quality/summary?table_limit=100").json()
        assert summary["tables"][0]["table_name"] == run.tables[0].table_name
        assert summary["tables"][0]["overall_score"] == 89.6

    def test_requires_a_databricks_connection(self, client):
        response = client.post("/api/v1/data-quality/catalog/run", json={
            "catalog": "catalog", "schema_name": "schema", "table": "table",
        })
        assert response.status_code == 409
        assert "Databricks connection" in response.json()["detail"]

    def test_direct_profiler_computes_selected_table_scores(self, monkeypatch):
        connection = DBConnection(
            name="Databricks", type="databricks",
            config={"server_hostname": "example", "http_path": "/sql/warehouse", "access_token": "x"},
        )

        class FakeConnector:
            closed = False

            def execute_query(self, query):
                if "SELECT DISTINCT" in query:
                    return [{"distinct_rows": 9}]
                return [{"__rows": 10, "p0": 8, "p1": 10,
                         "vn0": 8, "vp0": 7, "vn1": 10, "vp1": 9}]

            def close(self):
                self.closed = True

        fake = FakeConnector()
        monkeypatch.setattr(catalog_quality_service.SchemaService, "get_full_schema", lambda _connection: {
            "cat.sch.orders": [
                {"name": "email", "type": "varchar(180)"},
                {"name": "order_amount", "type": "decimal(12,2)"},
            ],
        })
        monkeypatch.setattr(catalog_quality_service, "get_connector", lambda _connection: fake)

        run = catalog_quality_service.profile_catalog_table(connection, "cat", "sch", "orders")
        table = run.tables[0]
        assert table.table_name == "cat.sch.orders"
        assert table.completeness == 90.0
        assert table.uniqueness == 90.0
        assert table.validity == 88.9
        assert {issue.check for issue in table.issues} == {"completeness", "uniqueness", "valid_email", "non_negative"}
        assert fake.closed is True


class TestUploadRuns:
    """Quick Upload runs (source="upload") must merge with, not replace, the Databricks job's results."""

    def _upload(self, client, table, completeness, when="2026-10-10T10:00:00Z"):
        return _post(client, {
            "run_id": f"upload:workspace.default.{table}", "source": "upload", "catalog": "workspace",
            "schema_name": "default", "completed_at": when,
            "tables": [{"table_name": f"default.{table}", "row_count": 10, "column_count": 3,
                        "completeness": completeness, "uniqueness": 100, "validity": None, "freshness": 100}],
        })

    def test_upload_does_not_hide_job_results(self, client):
        _post(client, _payload("job-1"))
        self._upload(client, "titanic", 92)
        summary = client.get("/api/v1/data-quality/summary").json()
        names = {t["table_name"] for t in summary["tables"]}
        assert names == {"workspace.sales.orders", "workspace.sales.customers", "default.titanic"}
        assert summary["latest"]["table_count"] == 3
        assert summary["latest"]["source"] == "databricks"
        assert [p["completed_at"][:10] for p in summary["trend"]] == ["2026-10-09"]  # trend = job runs only

    def test_uploads_alone_populate_dashboard(self, client):
        self._upload(client, "titanic", 92)
        self._upload(client, "orders", 100)
        summary = client.get("/api/v1/data-quality/summary").json()
        assert summary["latest"]["table_count"] == 2
        assert summary["latest"]["source"] == "upload"
        assert summary["tables"][0]["table_name"] == "default.titanic"  # lowest score first
        assert summary["latest"]["validity"] is None

    def test_reupload_replaces_previous_result(self, client):
        self._upload(client, "titanic", 50)
        self._upload(client, "titanic", 95, when="2026-10-11T10:00:00Z")
        summary = client.get("/api/v1/data-quality/summary").json()
        assert summary["latest"]["table_count"] == 1
        assert summary["tables"][0]["completeness"] == 95


class TestMultipleJobSources:
    """The table-profiling notebook job and the file pipeline both feed the dashboard; neither may hide the other."""

    def test_latest_run_of_each_source_is_merged(self, client):
        _post(client, _payload("nb-1"))  # source defaults to "databricks"
        _post(client, {
            "run_id": "files:2026-10-10T10:00:00+00:00", "source": "databricks-files", "catalog": "workspace",
            "schema_name": "dataone_dq", "completed_at": "2026-10-10T10:00:00Z",
            "tables": [{"table_name": "default.titanic", "row_count": 891, "column_count": 12, "completeness": 91.9,
                        "uniqueness": 100, "validity": 100, "freshness": 100}],
        })
        summary = client.get("/api/v1/data-quality/summary").json()
        assert {t["table_name"] for t in summary["tables"]} == {
            "workspace.sales.orders", "workspace.sales.customers", "default.titanic"}
        assert summary["latest"]["table_count"] == 3
        assert summary["latest"]["source"] == "databricks-files"  # most recent run heads the summary

    def test_older_run_of_same_source_is_superseded(self, client):
        for run_id, when, comp in (("f1", "2026-10-09T10:00:00Z", 50), ("f2", "2026-10-10T10:00:00Z", 90)):
            _post(client, {
                "run_id": run_id, "source": "databricks-files", "completed_at": when,
                "tables": [{"table_name": "default.titanic", "completeness": comp, "uniqueness": 100}],
            })
        summary = client.get("/api/v1/data-quality/summary").json()
        assert summary["latest"]["table_count"] == 1
        assert summary["tables"][0]["completeness"] == 90


class TestScope:
    """The headline averages every dataset; ``scope`` isolates uploaded files from warehouse tables."""

    def _seed(self, client):
        _post(client, _payload("nb-1"))  # warehouse tables (source "databricks"): 2 datasets
        _post(client, {
            "run_id": "files:1", "source": "databricks-files", "completed_at": "2026-10-10T10:00:00Z",
            "tables": [{"table_name": "default.titanic", "row_count": 891, "column_count": 12, "completeness": 91.9,
                        "uniqueness": 100, "validity": 100, "freshness": 100}],
        })

    def test_default_scope_is_unchanged_and_counts_are_reported(self, client):
        self._seed(client)
        s = client.get("/api/v1/data-quality/summary").json()
        assert s["scope"] == "all" and s["latest"]["table_count"] == 3
        assert s["scope_counts"] == {"all": 3, "files": 1, "tables": 2}

    def test_files_scope_shows_only_the_file_scores(self, client):
        self._seed(client)
        s = client.get("/api/v1/data-quality/summary?scope=files").json()
        assert s["latest"]["table_count"] == 1
        latest = s["latest"]
        assert (latest["completeness"], latest["uniqueness"], latest["validity"], latest["freshness"]) == (91.9, 100, 100, 100)
        assert [t["table_name"] for t in s["tables"]] == ["default.titanic"]

    def test_tables_scope_excludes_files_and_uploads(self, client):
        self._seed(client)
        client.post("/api/v1/data-quality/ingest", headers={"X-DataOne-Ingest-Key": KEY}, json={
            "run_id": "upload:x", "source": "upload", "completed_at": "2026-10-11T10:00:00Z",
            "tables": [{"table_name": "default.up", "completeness": 50}]})
        s = client.get("/api/v1/data-quality/summary?scope=tables").json()
        assert {t["table_name"] for t in s["tables"]} == {"workspace.sales.orders", "workspace.sales.customers"}
        assert s["scope_counts"]["files"] == 2  # titanic + the upload

    def test_empty_scope_returns_no_headline_but_still_counts(self, client):
        _post(client, _payload("nb-1"))
        s = client.get("/api/v1/data-quality/summary?scope=files").json()
        assert s["latest"] is None and s["scope_counts"] == {"all": 2, "files": 0, "tables": 2}

    def test_invalid_scope_rejected(self, client):
        assert client.get("/api/v1/data-quality/summary?scope=nope").status_code == 422
