# 02 — Lakebase storage

Use an attached Lakebase Autoscaling resource when its `PG*` and `LAKEBASE_ENDPOINT` variables are present. Generate short-lived database credentials with the Databricks SDK for each new pooled connection. If the resource is not attached yet, use a clearly reported, non-persistent SQLite setup database under `/tmp` so the UI can open and guide configuration rather than crash.

Done when storage selection is deterministic, tested, and visible in `/health`.

