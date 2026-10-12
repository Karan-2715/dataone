/**
 * TypeScript types matching the backend Pydantic schemas in
 * backend/app/schemas/mapping.py. Keep these in sync — the backend is
 * the source of truth.
 */

export type Role = "admin" | "analyst" | "viewer";

export interface SourceRef {
  table: string;
  column: string;
  type?: string | null;
  nullable?: boolean | null;
}

export interface TargetRef {
  table: string;
  column: string;
  type?: string | null;
  nullable?: boolean | null;
  primary_key?: boolean | null;
}

export type TransformationKind =
  | "direct"
  | "cast"
  | "concat"
  | "substring"
  | "coalesce"
  | "upper"
  | "lower"
  | "trim"
  | "default"
  | "null_if"
  | "lookup"
  | "case";

/** case's comparison operators. `==`/`!=` (not SQL's `=`/`<>`) match this
 * app's JSON-facing convention — transformation_grammar.py maps them to
 * SQL spelling at compile time. */
export type ComparisonOperator = ">" | ">=" | "<" | "<=" | "==" | "!=";

export type TransformationPayload =
  | { kind: "direct" }
  | { kind: "cast"; from: string; to: string }
  | {
      kind: "concat";
      parts: Array<
        | { kind: "literal"; value: string }
        | { kind: "source" }
      >;
    }
  | { kind: "substring"; source_index: number; start: number; length: number }
  | { kind: "coalesce"; fallback_kind: "literal"; fallback_value: string }
  | { kind: "upper" }
  | { kind: "lower" }
  | { kind: "trim" }
  | { kind: "default"; value_kind: "literal"; value: string }
  | { kind: "null_if"; equals: string }
  | {
      kind: "lookup";
      table: string;
      key_column: string;
      value_column: string;
      default?: string | null;
    }
  | {
      // Single-condition threshold conditional: IF source <operator>
      // compare_value THEN then_value ELSE else_value. E.g.
      // annual_revenue > 500000000 -> "Enterprise" else "SMB".
      kind: "case";
      operator: ComparisonOperator;
      compare_value: string | number;
      then_value: string | number;
      else_value: string | number;
    };

export type EdgeOrigin = "manual" | "ai_accepted" | "english_parsed";

export interface EdgeAudit {
  created_by?: string;
  created_at?: string;
  updated_by?: string;
  updated_at?: string;
}

export interface FieldMapping {
  id: number;
  mapping_id: number;
  target: TargetRef;
  sources: SourceRef[];
  transformation: TransformationPayload;
  origin: EdgeOrigin;
  ai_confidence?: number | null;
  audit: EdgeAudit;
  created_at: string;
  updated_at: string;
}

export type MappingStatus = "draft" | "published";

export type ReviewStage =
  | "draft"
  | "ai_proposed"
  | "pending_review"
  | "business_approved"
  | "steward_approved"
  | "production_ready"
  | "rejected";

export interface Mapping {
  id: number;
  name: string;
  source_id: number | null;
  target_id: number | null;
  status: MappingStatus;
  review_stage: ReviewStage;
  current_version_id: number | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  edges: FieldMapping[];
}

export type SuggestionStatus = "pending" | "accepted" | "rejected";

export interface AISuggestion {
  id: number;
  mapping_id: number;
  target_table: string;
  target_column: string;
  target_type: string | null;
  source_table: string;
  source_column: string;
  source_type: string | null;
  confidence: number;
  reason: string | null;
  components: Record<string, number> | null;
  suggested_transformation: TransformationPayload | null;
  transformation_note: string | null;
  status: SuggestionStatus;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
}

export type IssueVerdict = "ok" | "lossy_warning" | "blocking";

export interface ValidationIssue {
  edge_id: number | null;
  suggestion_id: number | null;
  verdict: IssueVerdict;
  message: string;
}

export interface ValidationResponse {
  mapping_id: number;
  ok_count: number;
  warning_count: number;
  blocking_count: number;
  issues: ValidationIssue[];
}

export interface PublishResponse {
  mapping_id: number;
  version_number: number;
  version_id: number;
  status: string;
  published_at: string;
  published_by: string;
}

export interface ConnectorRef {
  id: number;
  name: string;
  type: string;
}

/** Body for PUT /mappings/{id}/edges/{eid}/transformation */
export interface EdgeTransformationUpdate {
  transformation: TransformationPayload;
}

/** Body for POST /mappings/{id}/suggestions/{sid}/accept */
export interface SuggestionAcceptRequest {
  transformation?: TransformationPayload;
}

/**
 * Paginated list envelope matching the backend's MappingListResponse /
 * SuggestionListResponse shape (backend/app/schemas/mapping.py).
 */
export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

/** GET /mappings/{id}/versions item (E13-5/6). */
export interface VersionSummary {
  id: number;
  version_number: number;
  status: string;
  published_at: string | null;
  published_by: string | null;
  edge_count: number;
  is_current: boolean;
}

export interface VersionListResponse {
  items: VersionSummary[];
}

/** One field mapping as frozen into a version's edges_snapshot. */
export interface EdgeSnapshot {
  target: TargetRef;
  sources: SourceRef[];
  transformation: TransformationPayload;
  origin: EdgeOrigin;
  ai_confidence?: number | null;
}

export interface EdgeDiffItem {
  target_table: string;
  target_column: string;
  before: EdgeSnapshot;
  after: EdgeSnapshot;
}

export interface VersionDiffResponse {
  mapping_id: number;
  from_version: VersionSummary;
  to_version: VersionSummary;
  added: EdgeSnapshot[];
  removed: EdgeSnapshot[];
  changed: EdgeDiffItem[];
  unchanged_count: number;
}

export interface ExportArtifact {
  mapping_id: number;
  name: string;
  version: number;
  status: "published";
  published_at: string | null;
  published_by: string | null;
  source: { connection_id: number | null; name: string | null; type: string | null };
  target: { connection_id: number | null; name: string | null; type: string | null };
  field_mappings: Array<{
    id: number;
    origin: EdgeOrigin;
    ai_confidence: number | null;
    target: TargetRef;
    sources: SourceRef[];
    transformation: TransformationPayload;
    audit: EdgeAudit;
  }>;
  schema_snapshot: { source?: unknown; target?: unknown };
}
