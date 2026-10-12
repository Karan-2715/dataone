export interface AuditEvent {
  id: number;
  event_type: string;
  actor: string;
  module: string | null;
  target_type: string | null;
  target_id: string | null;
  target_name: string | null;
  before_summary: Record<string, unknown> | null;
  after_summary: Record<string, unknown> | null;
  correlation_id: string | null;
  outcome: string;
  summary: string | null;
  duration_ms: number | null;
  metadata: Record<string, unknown> | null;
  connection_id: number | null;
  connection_name: string | null;
  payload: Record<string, unknown> | null;
  status: string;
  event_hash: string | null;
  sequence: number | null;
  created_at: string;
}

export interface AuditFacets {
  modules: Record<string, number>;
  event_types: Record<string, number>;
  outcomes: Record<string, number>;
  actors: Record<string, number>;
  date_range: { earliest: string; latest: string } | null;
}

export interface AuditSearchResponse {
  events: AuditEvent[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
  facets: AuditFacets | null;
}

export interface AuditFilters {
  actor: string;
  module: string;
  event_type: string;
  outcome: string;
  date_from: string;
  date_to: string;
  search: string;
}

export const EMPTY_FILTERS: AuditFilters = {
  actor: "",
  module: "",
  event_type: "",
  outcome: "",
  date_from: "",
  date_to: "",
  search: "",
};

export type SortBy = "created_at" | "sequence" | "event_type" | "actor" | "module" | "outcome" | "duration_ms";
export type SortOrder = "asc" | "desc";
