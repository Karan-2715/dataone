# 03 — Data-quality resources

Allow the application to consume an attached SQL warehouse ID and data-quality job ID directly. Preserve the existing job-name lookup only for standalone/bundle deployments. In Databricks Apps mode, missing resources must raise an actionable configuration error rather than choosing the first accessible warehouse.

Done when ID-based execution is covered by tests and missing resources fail explicitly.

