// Response contract of GET /api/v1/dashboard/summary (dashboard_tasks #1).
// Mirrors backend/app/schemas/dashboard.py.

export type TimeRange = "24h" | "7d" | "30d";
export type DashboardView = "combined" | "executive" | "operational";

export type TileStatus = "loaded" | "error" | "unavailable";

export interface KPITileData {
  label: string;
  value: number;
  subtitle?: string | null;
  trend?: "up" | "down" | "neutral" | null;
  trend_label?: string | null;
  icon?: string | null;
  link_url: string;
  module: string;
  status: TileStatus;
  error_message?: string | null;
}

export interface FeedItemData {
  id: number;
  event_type: string;
  actor: string;
  module: string;
  summary: string;
  status: string;
  created_at: string;
  link_url: string | null;
}

export interface DashboardSummary {
  kpis: KPITileData[];
  feed: FeedItemData[];
  range: string;
  generated_at: string;
  available_views: DashboardView[];
}

// ── Data quality (Databricks PySpark job → /api/v1/data-quality) ─────────────
// Scores are 0–100; null means "not measured" and must render as such.
export type DQScore = number | null;

export interface DQIssue {
  column: string | null;
  check: string;
  detail: string;
}

export interface DQTable {
  table_name: string;
  row_count: number | null;
  column_count: number | null;
  completeness: DQScore;
  uniqueness: DQScore;
  validity: DQScore;
  freshness: DQScore;
  overall_score: DQScore;
  last_modified: string | null;
  issues: DQIssue[];
}

export interface DQRun {
  run_id: string;
  source: string;
  catalog: string | null;
  schema_name: string | null;
  table_count: number;
  total_rows: number | null;
  overall_score: DQScore;
  completeness: DQScore;
  uniqueness: DQScore;
  validity: DQScore;
  freshness: DQScore;
  completed_at: string;
}

export interface DQSummary {
  latest: DQRun | null;
  tables: DQTable[];
  trend: { completed_at: string; overall_score: DQScore }[];
  ingest_configured: boolean;
  /** "all" | "files" | "tables" — which datasets the headline averages */
  scope?: string;
  /** datasets per scope, so the UI can label the switch */
  scope_counts?: Record<string, number>;
}

// ── Workspace installer (/api/v1/databricks/install) ─────────────────────────
export interface InstallStep {
  step: string;
  status: "created" | "updated" | "unchanged" | "failed";
  detail: string;
}

export interface InstallStatus {
  workspace_host: string | null;
  signed_in_with_databricks: boolean;
  ingest_key_configured: boolean;
  current_release: string;
  installed: {
    catalog: string;
    schema_prefix: string;
    job_id: number | null;
    release: string;
    installed_by: string;
    installed_at: string;
    notebook_folder: string;
  } | null;
  up_to_date: boolean;
}

export interface InstallResult {
  ok: boolean;
  release: string;
  job_id: number | null;
  notebook_folder: string | null;
  steps: InstallStep[];
}
