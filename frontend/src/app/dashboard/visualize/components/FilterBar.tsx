"use client";
import type { CatalogTableRef, FilterOperator, FilterSpec } from "../lib/types";

const OPERATORS: Array<{ value: FilterOperator; label: string }> = [
  { value: "eq", label: "=" },
  { value: "neq", label: "≠" },
  { value: "gt", label: ">" },
  { value: "lt", label: "<" },
  { value: "gte", label: "≥" },
  { value: "lte", label: "≤" },
  { value: "contains", label: "contains" },
  { value: "between", label: "between" },
];

interface FilterBarProps {
  columns: CatalogTableRef["columns"];
  filters: FilterSpec[];
  onChange: (filters: FilterSpec[]) => void;
}

export default function FilterBar({ columns, filters, onChange }: FilterBarProps) {
  const addFilter = () => {
    if (columns.length === 0) return;
    onChange([...filters, { field: columns[0].column_name, operator: "eq", value: "" }]);
  };

  const updateFilter = (index: number, patch: Partial<FilterSpec>) => {
    onChange(filters.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  };

  const removeFilter = (index: number) => {
    onChange(filters.filter((_, i) => i !== index));
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs text-fg-subtle">Filters</span>
        <div className="flex items-center gap-2">
          {filters.length > 0 && (
            <button type="button" onClick={() => onChange([])} className="text-[11px] text-fg-subtle hover:text-fg-muted">
              Clear all
            </button>
          )}
          <button type="button" onClick={addFilter} className="text-[11px] text-info hover:text-info">
            + Add filter
          </button>
        </div>
      </div>
      <div className="flex flex-col gap-2">
        {filters.map((f, i) => (
          <div key={i} className="flex items-center gap-2">
            <select
              value={f.field}
              onChange={(e) => updateFilter(i, { field: e.target.value })}
              className="px-2 py-1.5 text-xs rounded-lg bg-surface-overlay border border-border-strong text-fg-muted"
            >
              {columns.map((c) => <option key={c.id} value={c.column_name}>{c.column_name}</option>)}
            </select>
            <select
              value={f.operator}
              onChange={(e) => updateFilter(i, { operator: e.target.value as FilterOperator })}
              className="px-2 py-1.5 text-xs rounded-lg bg-surface-overlay border border-border-strong text-fg-muted"
            >
              {OPERATORS.map((op) => <option key={op.value} value={op.value}>{op.label}</option>)}
            </select>
            {f.operator === "between" ? (
              <div className="flex items-center gap-1 flex-1">
                <input
                  type="text"
                  value={Array.isArray(f.value) ? String(f.value[0] ?? "") : ""}
                  onChange={(e) => updateFilter(i, { value: [e.target.value, Array.isArray(f.value) ? f.value[1] : ""] })}
                  placeholder="min"
                  className="w-full px-2 py-1.5 text-xs rounded-lg bg-surface-overlay border border-border-strong text-fg-muted"
                />
                <input
                  type="text"
                  value={Array.isArray(f.value) ? String(f.value[1] ?? "") : ""}
                  onChange={(e) => updateFilter(i, { value: [Array.isArray(f.value) ? f.value[0] : "", e.target.value] })}
                  placeholder="max"
                  className="w-full px-2 py-1.5 text-xs rounded-lg bg-surface-overlay border border-border-strong text-fg-muted"
                />
              </div>
            ) : (
              <input
                type="text"
                value={typeof f.value === "string" || typeof f.value === "number" ? String(f.value) : ""}
                onChange={(e) => updateFilter(i, { value: e.target.value })}
                placeholder="value"
                className="flex-1 px-2 py-1.5 text-xs rounded-lg bg-surface-overlay border border-border-strong text-fg-muted"
              />
            )}
            <button
              type="button"
              onClick={() => removeFilter(i)}
              aria-label="Remove filter"
              className="text-fg-subtle hover:text-danger text-xs px-1"
            >
              ✕
            </button>
          </div>
        ))}
        {filters.length === 0 && <p className="text-[11px] text-fg-subtle">No filters applied.</p>}
      </div>
    </div>
  );
}
