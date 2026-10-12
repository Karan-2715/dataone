"""File data-quality pipeline bridge: result parsing + the run / status / sync endpoints (Databricks mocked)."""
import pytest

from app.services import file_quality_service as fq

TABLE = "workspace.dataone_dq.file_quality_results"
# column order must match fq._RESULT_COLUMNS
ROW = ["default.titanic", "891", "12", "91.9", "100.0", "100.0", "100.0", "2026-10-12T06:03:34.000Z",
       '[{"column":"Cabin","check":"completeness","detail":"22.9% of values present (687 missing)"}]',
       "2026-10-12T06:10:00.000Z"]


class TestRowsToRun:
    def test_shapes_rows_as_a_dashboard_run(self):
        run = fq.rows_to_run([ROW], TABLE)
        assert run.source == "databricks-files" and run.catalog == "workspace" and run.schema_name == "dataone_dq"
        assert run.run_id.startswith("files:2026-10-12T06:10:00")
        t = run.tables[0]
        assert (t.table_name, t.row_count, t.column_count) == ("default.titanic", 891, 12)
        assert (t.completeness, t.uniqueness, t.validity, t.freshness) == (91.9, 100.0, 100.0, 100.0)
        assert t.issues[0].column == "Cabin" and t.last_modified.year == 2026

    def test_null_scores_stay_null_and_bad_issue_json_is_ignored(self):
        row = ["a.b", None, None, None, "100", None, None, None, "{not json", "2026-10-12T06:10:00Z"]
        t = fq.rows_to_run([row], TABLE).tables[0]
        assert t.completeness is None and t.validity is None and t.row_count is None and t.issues == []

    def test_no_rows_means_nothing_profiled_yet(self):
        assert fq.rows_to_run([], TABLE) is None

    def test_results_table_name_is_validated(self, monkeypatch):
        monkeypatch.setattr(fq.settings, "DQ_FILES_RESULTS_TABLE", "x; DROP TABLE y")
        with pytest.raises(fq.FileQualityError):
            fq.fetch_results()


class TestSaveOriginal:
    def test_only_pipeline_formats_are_saved(self, monkeypatch):
        uploaded = {}

        class _Files:
            def upload(self, path, data, overwrite):
                uploaded["path"], uploaded["bytes"], uploaded["overwrite"] = path, data.read(), overwrite

        monkeypatch.setattr(fq, "_client", lambda: type("C", (), {"files": _Files()})())
        assert fq.save_original_file("default", "titanic", "Titanic.CSV", b"id\n1\n") == \
            "/Volumes/workspace/dataone_dq/uploads/default/titanic.csv"
        assert uploaded == {"path": "/Volumes/workspace/dataone_dq/uploads/default/titanic.csv",
                            "bytes": b"id\n1\n", "overwrite": True}
        assert fq.save_original_file("default", "book", "book.xlsx", b"x") is None  # not profiled by the pipeline


class TestEndpoints:
    def test_run_returns_run_id(self, client, monkeypatch):
        monkeypatch.setattr(fq, "run_job", lambda: 4242)
        resp = client.post("/api/v1/data-quality/files/run")
        assert resp.status_code == 202 and resp.json() == {"run_id": 4242}

    def test_run_reports_missing_job(self, client, monkeypatch):
        def boom():
            raise fq.FileQualityError("job not found")
        monkeypatch.setattr(fq, "run_job", boom)
        resp = client.post("/api/v1/data-quality/files/run")
        assert resp.status_code == 404 and "job not found" in resp.json()["detail"]

    def test_status(self, client, monkeypatch):
        monkeypatch.setattr(fq, "get_run", lambda run_id: {"run_id": run_id, "finished": True, "success": True})
        assert client.get("/api/v1/data-quality/files/runs/7").json()["success"] is True

    def test_sync_populates_the_dashboard(self, client, monkeypatch):
        monkeypatch.setattr(fq, "fetch_results", lambda: fq.rows_to_run([ROW], TABLE))
        resp = client.post("/api/v1/data-quality/files/sync")
        assert resp.status_code == 200 and resp.json()["synced"] is True and resp.json()["tables"] == 1
        summary = client.get("/api/v1/data-quality/summary").json()
        assert summary["latest"]["source"] == "databricks-files"
        t = summary["tables"][0]
        assert (t["table_name"], t["completeness"], t["uniqueness"], t["validity"], t["freshness"]) == \
            ("default.titanic", 91.9, 100.0, 100.0, 100.0)
        # syncing the same pipeline refresh again must not duplicate it
        client.post("/api/v1/data-quality/files/sync")
        assert client.get("/api/v1/data-quality/summary").json()["latest"]["table_count"] == 1

    def test_sync_with_empty_results(self, client, monkeypatch):
        monkeypatch.setattr(fq, "fetch_results", lambda: None)
        assert client.post("/api/v1/data-quality/files/sync").json()["synced"] is False

    def test_viewer_cannot_run_or_sync(self, make_client, viewer, monkeypatch):
        monkeypatch.setattr(fq, "run_job", lambda: 1)
        c = make_client(viewer)
        assert c.post("/api/v1/data-quality/files/run").status_code == 403
        assert c.post("/api/v1/data-quality/files/sync").status_code == 403
