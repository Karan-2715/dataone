import pytest

from app.services import file_quality_service as fq


def test_run_uses_attached_job_id(monkeypatch):
    calls = []

    class _Jobs:
        def run_now(self, job_id):
            calls.append(job_id)
            return type("Run", (), {"run_id": 4242})()

    monkeypatch.setattr(fq.settings, "DQ_FILES_JOB_ID", "123")
    monkeypatch.setattr(fq, "_client", lambda: type("C", (), {"jobs": _Jobs()})())
    assert fq.run_job() == 4242
    assert calls == [123]


def test_native_mode_does_not_guess_a_job(monkeypatch):
    monkeypatch.setattr(fq.settings, "DQ_FILES_JOB_ID", None)
    monkeypatch.setattr(fq.settings, "DATABRICKS_NATIVE_MODE", True)
    monkeypatch.setattr(fq, "_client", lambda: object())
    with pytest.raises(fq.FileQualityError, match="not configured"):
        fq.run_job()


def test_native_mode_does_not_guess_a_warehouse(monkeypatch):
    monkeypatch.setattr(fq.settings, "DATABRICKS_SQL_WAREHOUSE_ID", None)
    monkeypatch.setattr(fq.settings, "DATABRICKS_NATIVE_MODE", True)
    with pytest.raises(fq.FileQualityError, match="SQL warehouse"):
        fq._run_sql(object(), "SELECT 1")
