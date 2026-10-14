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

### Marketplace / cloned-repository setup notebook

After cloning the full repository into a Databricks Git folder, open and run
`marketplace/DataOne_Setup.py`. The notebook automatically creates or updates:

* `workspace.dataone_dq` and its `uploads` managed volume;
* an existing accessible serverless SQL warehouse, or a small `DataOne SQL Warehouse` if none exists;
* `dataone-file-quality-pipeline` and the on-demand `dataone-file-quality-job`;
* the `DataOne NL2SQL and Data Quality` Genie space over measured quality results.

It runs the job once to materialize `workspace.dataone_dq.file_quality_results`, then prints the job, pipeline,
warehouse, and Genie IDs. In **+ New App**, attach the printed job (`CAN_MANAGE_RUN`), Genie space (`CAN_RUN`),
warehouse (`CAN_USE`), upload volume (`READ_VOLUME`, `WRITE_VOLUME`), and results table (`SELECT`). These grants
must happen after app creation because the app service principal does not exist before then.
Use resource key `genie-space` for the Genie space and `sql-warehouse` for the warehouse; `app.yml` maps those
resources to the backend environment variables.

The notebook does not ask for a source catalog/schema/table. Users select those dynamically in DataOne; NL2SQL
agents then include the selected Unity Catalog data plus the measured quality-results table. The default job is
on-demand (`UTC`); add a schedule in Databricks only when the workspace owner chooses an SLA and budget.

### Databricks Asset Bundle

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
