export interface Connection {
  id: number;
  name: string;
  type: string;
}

export interface AskDataAskResponse {
  session_id: string;
  sql: string | null;
  grounded: boolean;
  confidence: number;
  method: string;
  executed: boolean;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  masked_columns: string[];
  summary: string | null;
  warnings: string[];
  error: string | null;
  // Agentic DBA Copilot (agentic_dba_tasks #1/#3/#6/#10)
  intent?: "read_query" | "schema_design" | "ambiguous" | string;
  intent_confidence?: number;
  plan_id?: number | null;
  needs_clarification?: boolean;
}

// ── Agentic DBA plan artifact (agentic_dba_tasks #3/#6) ──────────────────

export interface ProposedColumn {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
  source_refs: { table: string; column: string; type?: string | null }[];
}

export interface ProposedTable {
  name: string;
  columns: ProposedColumn[];
  source_table?: string | null;
}

export interface DQRule {
  rule: string;
  target_table: string;
  target_column: string;
  justification: string;
  confidence: number;
  references?: { table: string; column: string };
}

export interface PlanTransformation {
  target_table: string;
  target_column: string;
  target_type?: string | null;
  target_nullable?: boolean | null;
  sources: { table: string; column: string }[];
  transformation: Record<string, unknown> | null;
  note?: string | null;
}

export interface GeneratedDDL {
  table: string;
  mode: "create" | "migrate";
  statements: string[];
}

export interface ApplyResult {
  table: string;
  mode?: string;
  status: "applied" | "failed" | "skipped" | "pending";
  error?: string | null;
  statements_executed?: number;
}

export interface SchemaDesignPlan {
  id: number;
  session_id: string | null;
  question: string;
  source_connection_id: number;
  target_connection_id: number | null;
  status:
    | "generating"
    | "ready"
    | "failed"
    | "rejected"
    | "applying"
    | "applied"
    | "partially_applied";
  domain_template: string | null;
  dialect: string | null;
  proposed_tables: ProposedTable[] | null;
  dq_rules: DQRule[] | null;
  transformations: PlanTransformation[] | null;
  generated_ddl: GeneratedDDL[] | null;
  confidence_notes: string[] | null;
  apply_results: ApplyResult[] | null;
  created_mapping_id: number | null;
  error: string | null;
  created_by: string | null;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  response?: AskDataAskResponse;
}

// ── Persisted session history (GET /askdata/sessions/{id}/messages) ──────
// Used to restore a conversation after the Query Workspace page remounts
// (e.g. the user navigates to another tab and back) — see AskDataView.

export interface SessionMessageEntry {
  id: number;
  role: "user" | "assistant";
  content: string;
  connection_id: number | null;
  sql_text: string | null;
  row_count: number | null;
  created_at: string;
}

export interface SessionMessagesResponse {
  session_id: string;
  messages: SessionMessageEntry[];
}
