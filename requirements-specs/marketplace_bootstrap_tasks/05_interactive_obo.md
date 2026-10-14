# 05 — Interactive Unity Catalog access

Use the Databricks Apps on-behalf-of token for interactive catalog discovery and Quick Upload SQL. Do not treat DataOne's own `Authorization` session token as a Databricks credential.

Background jobs and shared resources continue using the app service principal. Interactive writes therefore follow the signed-in user's existing Unity Catalog permissions, avoiding broad per-install grants over customer tables.

Done when token-routing tests pass and a deployed upload to a user-owned existing table succeeds without granting that table to the app service principal.
