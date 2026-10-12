"""Runs the pipeline's dataset function on real files with local Spark (no Databricks needed).

Requires pyspark>=4 and pyarrow:  pip install pyspark==4.0.1 pyarrow pytest
"""
import builtins
import importlib.util
import json
import os
import sys
import time
import types

import pytest

pytest.importorskip("pyspark")
pa = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "..", "src", "file_quality_pipeline.py")


@pytest.fixture(scope="module")
def spark():
    s = (SparkSession.builder.master("local[2]").appName("fq-test")
         .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "2").getOrCreate())
    yield s
    s.stop()


@pytest.fixture()
def pipeline(spark, tmp_path, monkeypatch):
    # Stand-in for pyspark.pipelines (only exists inside a Databricks pipeline runtime)
    stub = types.ModuleType("pyspark.pipelines")
    stub.materialized_view = lambda **kw: (lambda fn: fn)
    stub.expect_all = lambda rules: (lambda fn: fn)
    monkeypatch.setitem(sys.modules, "pyspark.pipelines", stub)
    import pyspark
    monkeypatch.setattr(pyspark, "pipelines", stub, raising=False)

    landing = tmp_path / "uploads"
    landing.mkdir()
    spark.conf.set("dataone.fq.landing_path", str(landing))
    spark.conf.set("dataone.fq.freshness_sla_hours", "24")
    monkeypatch.setattr(builtins, "spark", spark, raising=False)

    spec = importlib.util.spec_from_file_location("file_quality_pipeline", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, landing


def run(mod):
    return {r["file_name"]: r.asDict(recursive=True) for r in mod.file_quality_results().collect()}


def test_empty_landing_returns_no_rows(pipeline):
    mod, _ = pipeline
    assert run(mod) == {}


def test_csv_missing_duplicates_and_validity(pipeline):
    mod, landing = pipeline
    (landing / "default").mkdir()
    (landing / "default" / "people.csv").write_text(
        'id,age,email,note\n'
        '1,30,a@x.com,"hello, world"\n'
        '2,,not-an-email,\n'
        '3,-5,c@x.com,\n'
        '3,-5,c@x.com,\n'
    )
    r = run(mod)["people.csv"]
    assert r["table_name"] == "default.people" and r["file_format"] == "csv"
    assert r["row_count"] == 4 and r["column_count"] == 4
    # present cells: id 4, age 3, email 4, note 1 = 12 of 16
    assert r["completeness"] == 75.0
    assert r["uniqueness"] == 75.0  # 3 distinct of 4 rows
    # rules: age non_negative (3 present: 30 ok, -5, -5 fail -> 1/3), email valid (4 present, 3 ok)
    assert r["validity"] == round(100 * 4 / 7, 1)
    assert r["freshness"] == 100.0
    checks = {(i["column"], i["check"]) for i in r["issues"]}
    assert (None, "uniqueness") in checks and ("age", "non_negative") in checks
    assert ("email", "valid_email") in checks and ("note", "completeness") in checks


@pytest.mark.parametrize("name,writer", [
    ("a.json", lambda p: p.write_text(json.dumps([{"id": 1, "v": "x"}, {"id": 2, "v": None}]))),
    ("a.jsonl", lambda p: p.write_text('{"id":1,"v":"x"}\n{"id":2}\n')),
    ("a.tsv", lambda p: p.write_text("id\tv\n1\tx\n2\t\n")),
    ("a.parquet", lambda p: pq.write_table(pa.table({"id": [1, 2], "v": ["x", None]}), str(p))),
])
def test_every_format_is_profiled(pipeline, name, writer):
    mod, landing = pipeline
    writer(landing / name)
    r = run(mod)[name]
    assert r["row_count"] == 2 and r["column_count"] == 2
    assert r["completeness"] == 75.0 and r["uniqueness"] == 100.0
    assert r["validity"] is None  # no applicable rule -> not measured, never fabricated
    assert r["freshness"] == 100.0 and r["overall_score"] is not None


def test_old_file_is_stale(pipeline):
    mod, landing = pipeline
    f = landing / "old.csv"
    f.write_text("id\n1\n")
    two_days_ago = time.time() - 4 * 24 * 3600  # SLA 24h, full decay at 168h
    os.utime(f, (two_days_ago, two_days_ago))
    r = run(mod)["old.csv"]
    assert r["freshness"] == round(100 * (168 - 96) / (168 - 24), 1)
    assert any(i["check"] == "freshness" for i in r["issues"])


def test_corrupt_file_is_reported_not_dropped(pipeline):
    mod, landing = pipeline
    (landing / "bad.parquet").write_bytes(b"not parquet")
    (landing / "good.csv").write_text("id\n1\n")
    rows = run(mod)
    assert rows["bad.parquet"]["issues"][0]["check"] == "profile_error"
    assert rows["bad.parquet"]["completeness"] is None
    assert rows["good.csv"]["row_count"] == 1


def test_unsupported_and_hidden_files_ignored(pipeline):
    mod, landing = pipeline
    (landing / "notes.txt").write_text("x")
    (landing / "_tmp.csv").write_text("id\n1\n")
    assert run(mod) == {}


def test_table_name_for(pipeline):
    mod, _ = pipeline
    assert mod.table_name_for("default/Titanic Data-1.csv") == "default.titanic_data_1"
    assert mod.table_name_for("titanic.csv") == "titanic"
