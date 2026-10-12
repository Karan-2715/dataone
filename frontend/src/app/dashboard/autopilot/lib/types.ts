export type Role = "admin" | "analyst" | "viewer";

export interface AutopilotPolicyEntry {
  action_type: string;
  autonomy: "disabled" | "suggest" | "approve" | "auto";
  max_auto_per_hour: number;
  description: string;
  risk: "low" | "medium" | "high";
  reversible: boolean;
  reversibility_note: string;
  auto_capable: boolean;
  updated_by?: string | null;
}

export interface RecommendationRationale {
  summary?: string;
  evidence?: string[];
  trigger?: Record<string, unknown>;
}

export type RecommendationStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "superseded"
  | "executing"
  | "executed"
  | "failed";

export interface Recommendation {
  id: number;
  action_type: string;
  payload: Record<string, unknown>;
  subject: string;
  rationale: RecommendationRationale;
  confidence: number;
  risk: "low" | "medium" | "high";
  reversible: boolean;
  reversibility_note: string | null;
  status: RecommendationStatus;
  created_by: string;
  created_at: string;
  decided_by?: string | null;
  decided_at?: string | null;
  decision_mode?: string | null;
  modified_by?: string | null;
  modified_at?: string | null;
  execution_result?: Record<string, unknown> | null;
}

export interface ActionLogEntry {
  id: number;
  recommendation_id: number | null;
  action_type: string;
  payload: Record<string, unknown>;
  mode: "auto" | "approved";
  outcome:
    | "success"
    | "failure"
    | "blocked_prohibited"
    | "blocked_rate_limit"
    | "blocked_breaker"
    | "blocked_policy";
  detail: Record<string, unknown> | null;
  reversibility_note: string | null;
  actor: string;
  started_at: string;
  finished_at: string | null;
}

export interface Paginated<T> {
  total: number;
  items: T[];
}
