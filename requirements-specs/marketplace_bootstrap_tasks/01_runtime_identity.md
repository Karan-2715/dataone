# 01 — Runtime identity

Remove `APP_DATABASE_URL`, `SECRET_KEY`, `ADMIN_DEFAULT_PASSWORD`, and `DQ_INGEST_KEY` as mandatory secret-backed app resources. Derive the deployed JWT key from the injected Databricks app identity without logging it. Do not create a password-based bootstrap administrator in Databricks SSO mode unless an administrator deliberately configures a password.

Done when the app can start without a customer-created secret scope and request logs do not contain forwarded authorization headers.

