/**
 * Client-side group-by/reduce for charting a Query Workspace result set.
 *
 * Catalog mode delegates GROUP BY aggregation to the server
 * (backend/app/services/viz_service.py) because it has SQL to re-run.
 * An ad hoc Query Workspace result has no SQL to re-aggregate server-side
 * (see backend/app/services/query_execution_service.py — results are
 * never persisted), so this mirrors the same dimensions/measures/filters
 * shape and produces the same {columns, rows} shape ChartCanvas already
 * consumes, computed in the browser instead.
 */
import type { Aggregation, FilterSpec, MeasureSpec, VizQueryResponse } from "./types";

function toNumber(v: unknown): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

function reduceMeasure(aggregation: Aggregation, values: unknown[]): unknown {
  if (aggregation === "count") return values.length;
  const nums = values.map(toNumber);
  if (nums.length === 0) return null;
  switch (aggregation) {
    case "sum":
      return nums.reduce((a, b) => a + b, 0);
    case "avg":
      return nums.reduce((a, b) => a + b, 0) / nums.length;
    case "min":
      return Math.min(...nums);
    case "max":
      return Math.max(...nums);
    default:
      return null;
  }
}

function matchesFilter(row: Record<string, unknown>, filter: FilterSpec): boolean {
  const actual = row[filter.field];
  switch (filter.operator) {
    case "eq":
      return actual === filter.value;
    case "neq":
      return actual !== filter.value;
    case "gt":
      return toNumber(actual) > toNumber(filter.value);
    case "lt":
      return toNumber(actual) < toNumber(filter.value);
    case "gte":
      return toNumber(actual) >= toNumber(filter.value);
    case "lte":
      return toNumber(actual) <= toNumber(filter.value);
    case "contains":
      return String(actual ?? "").toLowerCase().includes(String(filter.value ?? "").toLowerCase());
    case "between": {
      const [lo, hi] = Array.isArray(filter.value) ? filter.value : [undefined, undefined];
      const n = toNumber(actual);
      return n >= toNumber(lo) && n <= toNumber(hi);
    }
    default:
      return true;
  }
}

export function aggregateQueryRows(
  rows: Record<string, unknown>[],
  dimensions: string[],
  measures: MeasureSpec[],
  filters: FilterSpec[],
): VizQueryResponse {
  const measureAliases = measures.map((m) => m.label || `${m.aggregation}_${m.field}`);
  const columns = [...dimensions, ...measureAliases];

  const filtered = filters.length === 0 ? rows : rows.filter((r) => filters.every((f) => matchesFilter(r, f)));

  if (dimensions.length === 0 && measures.length === 0) {
    return { columns, rows: [], row_count: 0, truncated: false };
  }

  const groups = new Map<string, { key: unknown[]; rows: Record<string, unknown>[] }>();
  for (const row of filtered) {
    const key = dimensions.map((d) => row[d]);
    const keyStr = JSON.stringify(key);
    const existing = groups.get(keyStr);
    if (existing) {
      existing.rows.push(row);
    } else {
      groups.set(keyStr, { key, rows: [row] });
    }
  }

  const outRows: unknown[][] = [];
  for (const group of groups.values()) {
    const measureValues = measures.map((m) => reduceMeasure(m.aggregation, group.rows.map((r) => r[m.field])));
    outRows.push([...group.key, ...measureValues]);
  }

  return { columns, rows: outRows, row_count: outRows.length, truncated: false };
}
