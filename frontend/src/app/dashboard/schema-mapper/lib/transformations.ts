/**
 * Client-side mirror of the backend's restricted transformation grammar.
 * Used to validate user input in the TransformEditor BEFORE sending to the
 * server, and to render the right form fields per `kind`.
 *
 * Source of truth: backend/app/services/transformation_grammar.py
 * (11 allow-listed kinds; no freeform DSL; structured JSON AST).
 */

import type {
  ComparisonOperator,
  TransformationKind,
  TransformationPayload,
} from "./types";

export const TRANSFORMATION_KINDS: readonly TransformationKind[] = [
  "direct",
  "cast",
  "concat",
  "substring",
  "coalesce",
  "upper",
  "lower",
  "trim",
  "default",
  "null_if",
  "lookup",
  "case",
] as const;

export const COMPARISON_OPERATORS: ReadonlyArray<{ value: ComparisonOperator; label: string }> = [
  { value: ">", label: "Greater than (>)" },
  { value: ">=", label: "Greater than or equal (>=)" },
  { value: "<", label: "Less than (<)" },
  { value: "<=", label: "Less than or equal (<=)" },
  { value: "==", label: "Equal to (=)" },
  { value: "!=", label: "Not equal to (≠)" },
];

export interface KindDescription {
  kind: TransformationKind;
  label: string;
  summary: string;
  /** Optional placeholder for the single primary input field (when the kind
   *  has a single string value, e.g. cast.from / cast.to / default.value). */
  primaryField?: string;
  requiredFields: string[];
  defaults?: Partial<TransformationPayload>;
}

export const KIND_DESCRIPTIONS: Record<TransformationKind, KindDescription> = {
  direct: {
    kind: "direct",
    label: "Direct",
    summary: "Use the source column as-is. No transformation.",
    requiredFields: [],
  },
  cast: {
    kind: "cast",
    label: "Format (Cast)",
    summary: "Convert source value to a target SQL type.",
    primaryField: "to",
    requiredFields: ["from", "to"],
    defaults: { kind: "cast", from: "TEXT", to: "TEXT" },
  },
  concat: {
    kind: "concat",
    label: "Concat",
    summary: "Concatenate literal strings and source columns.",
    requiredFields: ["parts"],
    defaults: { kind: "concat", parts: [{ kind: "literal", value: "" }] },
  },
  substring: {
    kind: "substring",
    label: "Substring",
    summary: "Take a slice of the N-th source column (N=source_index).",
    requiredFields: ["source_index", "start", "length"],
    defaults: { kind: "substring", source_index: 0, start: 0, length: 10 },
  },
  coalesce: {
    kind: "coalesce",
    label: "Coalesce (Fill NULL)",
    summary: "Replace NULL with a literal fallback value.",
    primaryField: "fallback_value",
    requiredFields: ["fallback_kind", "fallback_value"],
    defaults: { kind: "coalesce", fallback_kind: "literal", fallback_value: "" },
  },
  upper: {
    kind: "upper",
    label: "Upper",
    summary: "Convert to uppercase.",
    requiredFields: [],
  },
  lower: {
    kind: "lower",
    label: "Lower",
    summary: "Convert to lowercase.",
    requiredFields: [],
  },
  trim: {
    kind: "trim",
    label: "Trim",
    summary: "Trim leading and trailing whitespace.",
    requiredFields: [],
  },
  default: {
    kind: "default",
    label: "Default",
    summary: "If the source value is NULL, fall back to a literal default.",
    primaryField: "value",
    requiredFields: ["value_kind", "value"],
    defaults: { kind: "default", value_kind: "literal", value: "" },
  },
  null_if: {
    kind: "null_if",
    label: "Null If",
    summary: "If the source value equals a literal, produce NULL.",
    primaryField: "equals",
    requiredFields: ["equals"],
    defaults: { kind: "null_if", equals: "" },
  },
  lookup: {
    kind: "lookup",
    label: "Lookup",
    summary:
      "Resolve the source value against an auxiliary table (SELECT value_column FROM table WHERE key_column = source).",
    primaryField: "table",
    requiredFields: ["table", "key_column", "value_column"],
    defaults: { kind: "lookup", table: "", key_column: "", value_column: "" },
  },
  case: {
    kind: "case",
    label: "Conditional (If/Else)",
    summary:
      "If the source value meets a threshold, use one value, otherwise use another — e.g. annual_revenue > 500000000 → \"Enterprise\" else \"SMB\".",
    requiredFields: ["operator", "compare_value", "then_value", "else_value"],
    defaults: { kind: "case", operator: ">", compare_value: 0, then_value: "", else_value: "" },
  },
};

export function blankTransformation(kind: TransformationKind): TransformationPayload {
  const desc = KIND_DESCRIPTIONS[kind];
  if (desc.defaults) {
    return JSON.parse(JSON.stringify(desc.defaults)) as TransformationPayload;
  }
  return { kind } as TransformationPayload;
}

export interface GrammarIssue {
  field: string;
  message: string;
}

/**
 * Lightweight client-side grammar validation. The server is the final
 * authority — this is purely for instant feedback in the TransformEditor.
 */
export function validateTransformation(
  payload: TransformationPayload | null | undefined,
): GrammarIssue[] {
  const issues: GrammarIssue[] = [];
  if (!payload || typeof payload !== "object" || !("kind" in payload)) {
    issues.push({ field: "kind", message: "transformation must include a kind" });
    return issues;
  }
  if (!TRANSFORMATION_KINDS.includes(payload.kind)) {
    issues.push({ field: "kind", message: `unknown kind '${payload.kind}'` });
    return issues;
  }
  const required = KIND_DESCRIPTIONS[payload.kind].requiredFields;
  for (const f of required) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const v = (payload as any)[f];
    if (v === undefined || v === null || v === "") {
      issues.push({ field: f, message: `${f} is required` });
    }
  }
  if (payload.kind === "concat") {
    if (!Array.isArray(payload.parts) || payload.parts.length === 0) {
      issues.push({ field: "parts", message: "parts must be a non-empty list" });
    } else {
      payload.parts.forEach((p, i) => {
        if (p.kind !== "literal" && p.kind !== "source") {
          issues.push({
            field: `parts[${i}].kind`,
            message: `part kind must be 'literal' or 'source'`,
          });
        }
        if (p.kind === "literal" && (typeof p.value !== "string" || p.value === "")) {
          issues.push({
            field: `parts[${i}].value`,
            message: `literal part needs a non-empty value`,
          });
        }
      });
    }
  }
  if (payload.kind === "substring") {
    if (typeof payload.source_index !== "number" || payload.source_index < 0) {
      issues.push({ field: "source_index", message: "source_index must be >= 0" });
    }
    if (typeof payload.start !== "number" || payload.start < 0) {
      issues.push({ field: "start", message: "start must be >= 0" });
    }
    if (typeof payload.length !== "number" || payload.length <= 0) {
      issues.push({ field: "length", message: "length must be > 0" });
    }
  }
  return issues;
}
