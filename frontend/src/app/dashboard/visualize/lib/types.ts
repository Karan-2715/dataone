/**
 * Types for the Visualize charting page (frontend_tasks/01_visualize_charting.md).
 * Mirrors backend/app/schemas/viz.py field-for-field.
 */

export type Role = "admin" | "analyst" | "viewer";

export type ChartType = "table" | "bar" | "line" | "area" | "pie" | "scatter" | "kpi";
export type Aggregation = "sum" | "avg" | "count" | "min" | "max";
export type FilterOperator = "eq" | "neq" | "gt" | "lt" | "gte" | "lte" | "contains" | "between";

export interface ConnectorRef {
  id: number;
  name: string;
  type: string;
}

export interface CatalogColumnRef {
  id: number;
  column_name: string;
  data_type: string | null;
}

export interface CatalogTableRef {
  id: number;
  table_name: string;
  columns: CatalogColumnRef[];
}

export interface MeasureSpec {
  field: string;
  aggregation: Aggregation;
  label?: string | null;
}

export interface FilterSpec {
  field: string;
  operator: FilterOperator;
  value: unknown;
}

export interface VizQueryRequest {
  connection_id: number;
  table_name: string;
  dimensions: string[];
  measures: MeasureSpec[];
  filters: FilterSpec[];
}

export interface VizQueryResponse {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
}

export interface VizView {
  id: number;
  name: string;
  connection_id: number;
  table_name: string;
  chart_type: ChartType;
  dimensions: string[];
  measures: MeasureSpec[];
  filters: FilterSpec[];
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface VizViewListResponse {
  items: VizView[];
  total: number;
}
