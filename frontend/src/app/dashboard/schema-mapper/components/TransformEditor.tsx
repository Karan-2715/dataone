"use client";
import { useEffect, useMemo, useState } from "react";
import { classNames } from "../lib/format";
import {
  COMPARISON_OPERATORS,
  KIND_DESCRIPTIONS,
  TRANSFORMATION_KINDS,
  blankTransformation,
  validateTransformation,
} from "../lib/transformations";
import type {
  ComparisonOperator,
  TransformationKind,
  TransformationPayload,
} from "../lib/types";

// A user typing "500000000" into a plain text box means the number
// 500000000, not the string "500000000" — comparing an int source value
// against a string threshold raises a Python TypeError server-side (both
// at preview time and during real pipeline execution) and would silently
// never match any row. Auto-detect: a numeric-looking, non-empty string
// becomes a number; anything else (e.g. "Enterprise") stays a string.
function coerceLiteral(raw: string): string | number {
  const trimmed = raw.trim();
  if (trimmed === "") return raw;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : raw;
}

interface TransformEditorProps {
  initial: TransformationPayload;
  onCancel: () => void;
  onApply: (next: TransformationPayload) => void;
}

export default function TransformEditor({
  initial,
  onCancel,
  onApply,
}: TransformEditorProps) {
  const [kind, setKind] = useState<TransformationKind>(initial.kind);
  const [payload, setPayload] = useState<TransformationPayload>(initial);

  // When kind changes, reset the payload to that kind's defaults (preserving
  // "from" for cast when switching to a similar kind, but starting fresh is
  // simpler and matches the backend grammar semantics).
  useEffect(() => {
    if (payload.kind !== kind) {
      setPayload(blankTransformation(kind));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);

  const issues = useMemo(() => validateTransformation(payload), [payload]);
  const desc = KIND_DESCRIPTIONS[kind];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Edit transformation"
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="w-full max-w-2xl rounded-xl bg-surface border border-border p-6 shadow-2xl">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold text-fg">
              Edit Transformation
            </h2>
            <p className="text-xs text-fg-subtle mt-1">
              {desc.summary}
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="text-fg-subtle hover:text-fg-muted text-sm"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="mt-5">
          <label className="text-xs text-fg-subtle">
            Kind
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as TransformationKind)}
              className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm text-fg focus:outline-none focus:border-accent"
            >
              {TRANSFORMATION_KINDS.map((k) => (
                <option key={k} value={k}>
                  {KIND_DESCRIPTIONS[k].label} — {KIND_DESCRIPTIONS[k].summary}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="mt-4">
          <KindFields kind={kind} payload={payload} onChange={setPayload} />
        </div>

        {issues.length > 0 && (
          <div className="mt-4 rounded-lg border border-warning/20 bg-warning/5 p-3">
            <div className="text-xs font-semibold text-warning mb-1">
              {issues.length} issue{issues.length === 1 ? "" : "s"}
            </div>
            <ul className="text-[11px] text-warning space-y-0.5">
              {issues.map((iss, i) => (
                <li key={i}>
                  <span className="font-mono text-warning">{iss.field}</span>: {iss.message}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-sm text-fg-subtle hover:text-fg-muted rounded-lg"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onApply(payload)}
            disabled={issues.length > 0}
            className={classNames(
              "px-4 py-2 text-sm font-semibold rounded-lg",
              issues.length > 0
                ? "bg-surface-overlay text-fg-subtle cursor-not-allowed"
                : "bg-gradient-to-r from-blue-500 to-white/20 text-white hover:opacity-90",
            )}
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}

function KindFields({
  kind,
  payload,
  onChange,
}: {
  kind: TransformationKind;
  payload: TransformationPayload;
  onChange: (next: TransformationPayload) => void;
}) {
  const set = (key: string, value: unknown) => {
    onChange({ ...payload, [key]: value } as TransformationPayload);
  };

  if (kind === "direct" || kind === "upper" || kind === "lower" || kind === "trim") {
    return (
      <div className="text-xs text-fg-subtle italic px-3 py-2 rounded bg-background/50 border border-border">
        No parameters — this kind applies the operation to the source column directly.
      </div>
    );
  }

  if (kind === "cast") {
    const p = payload as Extract<TransformationPayload, { kind: "cast" }>;
    return (
      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs text-fg-subtle">
          From type
          <input
            type="text"
            value={p.from}
            onChange={(e) => set("from", e.target.value)}
            placeholder="TEXT"
            className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
          />
        </label>
        <label className="text-xs text-fg-subtle">
          To type
          <input
            type="text"
            value={p.to}
            onChange={(e) => set("to", e.target.value)}
            placeholder="INTEGER"
            className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
          />
        </label>
      </div>
    );
  }

  if (kind === "concat") {
    const p = payload as Extract<TransformationPayload, { kind: "concat" }>;
    return (
      <div className="space-y-2">
        <div className="text-xs text-fg-subtle">Parts (in order)</div>
        {p.parts.map((part, i) => (
          <div key={i} className="flex items-center gap-2">
            <select
              value={part.kind}
              onChange={(e) => {
                const k = e.target.value as "literal" | "source";
                const next = [...p.parts];
                next[i] =
                  k === "literal"
                    ? { kind: "literal", value: "" }
                    : { kind: "source" };
                onChange({ ...payload, parts: next } as TransformationPayload);
              }}
              className="px-2 py-1.5 rounded bg-surface-overlay border border-border-strong text-xs"
            >
              <option value="literal">literal</option>
              <option value="source">source</option>
            </select>
            {part.kind === "literal" ? (
              <input
                type="text"
                value={part.value}
                onChange={(e) => {
                  const next = [...p.parts];
                  next[i] = { kind: "literal", value: e.target.value };
                  onChange({ ...payload, parts: next } as TransformationPayload);
                }}
                placeholder="literal text"
                className="flex-1 px-3 py-1.5 rounded bg-surface-overlay border border-border-strong text-sm font-mono"
              />
            ) : (
              <span className="flex-1 text-xs text-fg-subtle italic">
                uses the N-th source column (position {i} in parts)
              </span>
            )}
            <button
              type="button"
              onClick={() => {
                const next = p.parts.filter((_, idx) => idx !== i);
                onChange({ ...payload, parts: next } as TransformationPayload);
              }}
              className="px-2 py-1 text-xs text-fg-subtle hover:text-danger"
              aria-label="Remove part"
            >
              ✕
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => {
            const next = [...p.parts, { kind: "literal" as const, value: "" }];
            onChange({ ...payload, parts: next } as TransformationPayload);
          }}
          className="text-xs text-info hover:underline"
        >
          + Add part
        </button>
      </div>
    );
  }

  if (kind === "substring") {
    const p = payload as Extract<TransformationPayload, { kind: "substring" }>;
    return (
      <div className="grid grid-cols-3 gap-3">
        <label className="text-xs text-fg-subtle">
          Source index
          <input
            type="number"
            min={0}
            value={p.source_index}
            onChange={(e) => set("source_index", Number(e.target.value))}
            className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
          />
        </label>
        <label className="text-xs text-fg-subtle">
          Start (0-based)
          <input
            type="number"
            min={0}
            value={p.start}
            onChange={(e) => set("start", Number(e.target.value))}
            className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
          />
        </label>
        <label className="text-xs text-fg-subtle">
          Length
          <input
            type="number"
            min={1}
            value={p.length}
            onChange={(e) => set("length", Number(e.target.value))}
            className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
          />
        </label>
      </div>
    );
  }

  if (kind === "coalesce") {
    const p = payload as Extract<TransformationPayload, { kind: "coalesce" }>;
    return (
      <label className="text-xs text-fg-subtle">
        Fallback value (literal)
        <input
          type="text"
          value={p.fallback_value}
          onChange={(e) => set("fallback_value", e.target.value)}
          placeholder="n/a"
          className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
        />
      </label>
    );
  }

  if (kind === "default") {
    const p = payload as Extract<TransformationPayload, { kind: "default" }>;
    return (
      <label className="text-xs text-fg-subtle">
        Default value (literal)
        <input
          type="text"
          value={String(p.value)}
          onChange={(e) => set("value", e.target.value)}
          placeholder="unknown"
          className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
        />
      </label>
    );
  }

  if (kind === "null_if") {
    const p = payload as Extract<TransformationPayload, { kind: "null_if" }>;
    return (
      <label className="text-xs text-fg-subtle">
        Equals (literal)
        <input
          type="text"
          value={String(p.equals)}
          onChange={(e) => set("equals", e.target.value)}
          placeholder=""
          className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
        />
      </label>
    );
  }

  if (kind === "lookup") {
    const p = payload as Extract<TransformationPayload, { kind: "lookup" }>;
    return (
      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs text-fg-subtle">
          Lookup table
          <input
            type="text"
            value={p.table}
            onChange={(e) => set("table", e.target.value)}
            placeholder="lu_country"
            className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
          />
        </label>
        <label className="text-xs text-fg-subtle">
          Key column
          <input
            type="text"
            value={p.key_column}
            onChange={(e) => set("key_column", e.target.value)}
            placeholder="code"
            className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
          />
        </label>
        <label className="text-xs text-fg-subtle">
          Value column
          <input
            type="text"
            value={p.value_column}
            onChange={(e) => set("value_column", e.target.value)}
            placeholder="name"
            className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
          />
        </label>
        <label className="text-xs text-fg-subtle">
          Default (optional)
          <input
            type="text"
            value={p.default ?? ""}
            onChange={(e) => set("default", e.target.value || null)}
            placeholder="UNK"
            className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
          />
        </label>
      </div>
    );
  }

  if (kind === "case") {
    const p = payload as Extract<TransformationPayload, { kind: "case" }>;
    return (
      <div className="space-y-3">
        <div className="flex items-end gap-2">
          <span className="text-xs text-fg-subtle pb-2">If source value is</span>
          <label className="text-xs text-fg-subtle">
            Operator
            <select
              value={p.operator}
              onChange={(e) => set("operator", e.target.value as ComparisonOperator)}
              className="mt-1 px-2 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm"
            >
              {COMPARISON_OPERATORS.map((op) => (
                <option key={op.value} value={op.value}>
                  {op.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex-1 text-xs text-fg-subtle">
            Compare to
            <input
              type="text"
              value={String(p.compare_value)}
              onChange={(e) => set("compare_value", coerceLiteral(e.target.value))}
              placeholder="500000000"
              className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs text-fg-subtle">
            Then use
            <input
              type="text"
              value={String(p.then_value)}
              onChange={(e) => set("then_value", coerceLiteral(e.target.value))}
              placeholder="Enterprise"
              className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
            />
          </label>
          <label className="text-xs text-fg-subtle">
            Else use
            <input
              type="text"
              value={String(p.else_value)}
              onChange={(e) => set("else_value", coerceLiteral(e.target.value))}
              placeholder="SMB"
              className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm font-mono"
            />
          </label>
        </div>
        <p className="text-[11px] text-fg-subtle italic">
          Numbers typed here (e.g. 500000000) are compared as numbers, not text — a value like &quot;Enterprise&quot; stays text.
        </p>
      </div>
    );
  }

  return null;
}
