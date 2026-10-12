# DataOne file data quality (Databricks pipeline + job)

Profiles data files and publishes **completeness, uniqueness, validity and freshness** per file for DataOne's
*Data Quality* dashboard.

```
Quick Upload ──┐                                   ┌─ file_quality_results (UC table, one row per file)
               ├─► /Volumes/<catalog>/dataone_dq/uploads/<schema>/<table>.<ext>
Databricks UI ─┘          │ file arrives            │
 / CLI drop               ▼                         │ read by the DataOne app (service principal)
                 dataone-file-quality-job ─► dataone-file-quality-pipeline ─┘
```

| Resource | Name | Purpose |
|---|---|---|
| Schema | `workspace.dataone_dq` | results + landing volume |
| Volume | `workspace.dataone_dq.uploads` | landing zone (csv, tsv, json, jsonl/ndjson, parquet, avro, orc) |
| Pipeline | `dataone-file-quality-pipeline` | serverless Lakeflow pipeline, materialized view `file_quality_results` |
| Job | `dataone-file-quality-job` | runs the pipeline when a file lands (file-arrival trigger) or on demand |

Independent of the `dataone-secure` bundle (`dataone-orchestrator`, `dataone-quality-pipeline`).

## Deploy

```bash
databricks bundle validate --profile <profile>
databricks bundle deploy   --profile <profile>
databricks bundle run file_quality_job --profile <profile>   # optional: run now
```

Variables (`databricks.yml`): `catalog`, `schema`, `volume`, `freshness_sla_hours`, and `app_service_principal`
(the DataOne Databricks App's service principal — it is granted WRITE on the volume, SELECT on the schema and
CAN_MANAGE_RUN on the job). Point the app at a different location with `DQ_FILES_VOLUME_PATH`,
`DQ_FILES_RESULTS_TABLE` and `DQ_FILES_JOB_NAME`.

## How scores are computed (0-100, `NULL` = not measurable, never invented)

| Dimension | Definition |
|---|---|
| Completeness | % of cells present — NULL, NaN and blank strings are missing |
| Uniqueness | % of rows that are distinct (exact duplicates lower it) |
| Validity | % of non-blank values passing rules inferred from column names: email, phone, non-negative amounts/ages/counts, dates not in the future. `NULL` when no column has an applicable rule |
| Freshness | file age vs `freshness_sla_hours`: 100 within the SLA, falling linearly to 0 at 7× the SLA (as of the last pipeline run) |

Same definitions as `backend/app/databricks_assets/notebooks/dataone_data_quality.py`, so file and table scores compare.
An unreadable file is reported with no scores and a `profile_error` issue instead of being dropped.

## Using it

* **Quick Upload** (Connectors page) saves the original file to the volume as `<schema>/<table>.<ext>`.
* **From Databricks**: copy a file into the volume, e.g.
  `databricks fs cp orders.parquet dbfs:/Volumes/workspace/dataone_dq/uploads/sales/orders.parquet`.
  Files directly in the volume root are named after the file; `<folder>/<file>` becomes `folder.file`.
* The job starts on its own about a minute after a file lands. In DataOne open **Dashboard → Data quality** and use
  **Sync results** (pull the latest) or **Check uploaded files** (run the job, then sync).

## Tests

The profiling logic runs on a local Spark session — no Databricks needed:

```bash
pip install pyspark==4.0.1 pyarrow pytest
pytest tests
```
