export interface Connection {
  id: number;
  name: string;
  type: string;
}

export interface QueryExecuteResult {
  statement_type: string;
  tables_referenced: string[];
  warnings: string[];
  requires_confirmation: boolean;
  executed: boolean;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  affected_rows: number | null;
  page: number;
  page_size: number;
  has_more: boolean;
  truncated: boolean;
  duration_ms: number | null;
  error: string | null;
}

export interface SavedQuery {
  id: number;
  connection_id: number;
  name: string;
  sql_text: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface HistoryEntry {
  id: number;
  actor: string;
  sql: string | null;
  connection_id: string | null;
  statement_type: string | null;
  outcome: string;
  row_count: number | null;
  duration_ms: number | null;
  created_at: string;
}

export interface HistoryResponse {
  history: HistoryEntry[];
  total: number;
  page: number;
  page_size: number;
}

export interface CatalogColumn {
  column_name: string;
}

export interface CatalogTable {
  table_name: string;
  columns: CatalogColumn[];
}

export interface CatalogTableListResponse {
  connection_id: number;
  tables: CatalogTable[];
}
