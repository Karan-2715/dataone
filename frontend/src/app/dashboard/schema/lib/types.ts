/**
 * Types for the Schema Intel Catalog page (schema_intel_tasks Task #5).
 * Mirrors backend/app/schemas/schema_catalog.py field-for-field.
 */

export type Role = "admin" | "analyst" | "viewer";

export type ClassificationLabel = "PII" | "Sensitive" | "Public";
export type ClassificationMethod = "keyword" | "value_pattern" | "manual_override";

export interface ConnectorRef {
  id: number;
  name: string;
  type: string;
}

export interface CatalogForeignKey {
  references_table: string;
  references_column: string;
}

export interface ColumnProfile {
  null_count: number;
  null_rate: number;
  distinct_count: number | null;
  min_value: string | null;
  max_value: string | null;
  sample_size_used: number;
  profiled_at: string;
}

export interface ColumnClassification {
  label: ClassificationLabel;
  level: "High" | "Medium" | "Low";
  confidence: number;
  method: ClassificationMethod;
  overridden_by: string | null;
  overridden_at: string | null;
  classified_at: string;
}

export interface CatalogColumn {
  id: number;
  column_name: string;
  data_type: string | null;
  nullable: boolean;
  is_primary_key: boolean;
  ordinal_position: number;
  foreign_keys: CatalogForeignKey[];
  profile: ColumnProfile | null;
  classification: ColumnClassification | null;
}

export interface CatalogTable {
  id: number;
  connection_id: number;
  table_name: string;
  last_scanned_at: string;
  columns: CatalogColumn[];
}

export interface CatalogTableListResponse {
  connection_id: number;
  tables: CatalogTable[];
}

export interface ScanResult {
  connection_id: number;
  tables_scanned: number;
  columns_scanned: number;
  scanned_at: string;
}

export interface ProfileEnqueueResult {
  status: string;
  task_id: string | null;
  message: string;
}

export interface DriftEventSummary {
  id: number;
  tables_added: string[];
  tables_removed: string[];
  columns_added: Record<string, string[]>;
  columns_removed: Record<string, string[]>;
  type_changes: Record<string, string[]>;
  detected_at: string;
}

export interface SchemaSnapshotSummary {
  id: number;
  schema_hash: string;
  captured_at: string;
  table_count: number;
  drift_event: DriftEventSummary | null;
}

export interface DriftHistoryResponse {
  connection: string;
  snapshots: SchemaSnapshotSummary[];
}
