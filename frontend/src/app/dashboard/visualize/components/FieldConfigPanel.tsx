"use client";
import { useState, useMemo, useEffect } from "react";
import { classNames } from "../lib/format";
import type { Aggregation, CatalogTableRef, MeasureSpec } from "../lib/types";

const AGGREGATIONS: Aggregation[] = ["sum", "avg", "count", "min", "max"];

interface FieldConfigPanelProps {
  catalogTables: CatalogTableRef[];
  catalogLoading: boolean;
  tableName: string | null;
  onTableChange: (name: string) => void;
  dimensions: string[];
  onDimensionsChange: (dims: string[]) => void;
  measures: MeasureSpec[];
  onMeasuresChange: (measures: MeasureSpec[]) => void;
}

export default function FieldConfigPanel({
  catalogTables, catalogLoading, tableName, onTableChange,
  dimensions, onDimensionsChange, measures, onMeasuresChange,
}: FieldConfigPanelProps) {
  const selectedTable = catalogTables.find((t) => t.table_name === tableName) ?? null;
  const columns = selectedTable?.columns ?? [];

  const [selectedCatalog, setSelectedCatalog] = useState<string>("");
  const [selectedSchema, setSelectedSchema] = useState<string>("");

  useEffect(() => {
    if (tableName) {
      const parts = tableName.split('.');
      if (parts.length === 3) {
        setSelectedCatalog(parts[0]);
        setSelectedSchema(parts[1]);
      } else if (parts.length === 2) {
        setSelectedCatalog("default");
        setSelectedSchema(parts[0]);
      } else {
        setSelectedCatalog("default");
        setSelectedSchema("default");
      }
    }
  }, [tableName]);

  const hierarchy = useMemo(() => {
    const h: Record<string, Record<string, { display: string, full: string }[]>> = {};
    catalogTables.forEach(t => {
      const parts = t.table_name.split('.');
      let c = 'default', s = 'default', tbl = t.table_name;
      if (parts.length === 3) { c = parts[0]; s = parts[1]; tbl = parts[2]; }
      else if (parts.length === 2) { c = 'default'; s = parts[0]; tbl = parts[1]; }
      
      if (!h[c]) h[c] = {};
      if (!h[c][s]) h[c][s] = [];
      h[c][s].push({ display: tbl, full: t.table_name });
    });
    return h;
  }, [catalogTables]);

  const availableCatalogs = Object.keys(hierarchy);
  const availableSchemas = selectedCatalog && hierarchy[selectedCatalog] ? Object.keys(hierarchy[selectedCatalog]) : [];
  const availableTables = selectedCatalog && selectedSchema && hierarchy[selectedCatalog]?.[selectedSchema] ? hierarchy[selectedCatalog][selectedSchema] : [];

  const showCatalog = availableCatalogs.length > 1 || (availableCatalogs.length === 1 && availableCatalogs[0] !== "default");
  const showSchema = availableSchemas.length > 1 || (availableSchemas.length === 1 && availableSchemas[0] !== "default");

  const toggleDimension = (field: string) => {
    onDimensionsChange(
      dimensions.includes(field) ? dimensions.filter((d) => d !== field) : [...dimensions, field],
    );
  };

  const addMeasure = () => {
    if (columns.length === 0) return;
    onMeasuresChange([...measures, { field: columns[0].column_name, aggregation: "sum" }]);
  };

  const updateMeasure = (index: number, patch: Partial<MeasureSpec>) => {
    onMeasuresChange(measures.map((m, i) => (i === index ? { ...m, ...patch } : m)));
  };

  const removeMeasure = (index: number) => {
    onMeasuresChange(measures.filter((_, i) => i !== index));
  };

  if (catalogLoading) {
    return <div className="text-xs text-fg-subtle">Loading catalog…</div>;
  }

  if (catalogTables.length === 0) {
    return (
      <div className="text-xs text-warning bg-warning/10 border border-warning/20 rounded-lg px-3 py-2">
        No catalog found for this connection. Scan it in{" "}
        <a href="/dashboard/schema" className="underline">Schema Intel</a> first.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        {showCatalog && (
          <label className="text-xs text-fg-subtle">
            Catalog
            <select
              value={selectedCatalog}
              onChange={(e) => {
                const newCat = e.target.value;
                setSelectedCatalog(newCat);
                const firstSchema = Object.keys(hierarchy[newCat] || {})[0];
                setSelectedSchema(firstSchema || "");
                const firstTable = hierarchy[newCat]?.[firstSchema]?.[0]?.full;
                if (firstTable) onTableChange(firstTable);
              }}
              className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm text-fg focus:outline-none focus:border-accent"
            >
              <option value="" disabled>Select Catalog</option>
              {availableCatalogs.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
        )}

        {showSchema && (
          <label className="text-xs text-fg-subtle">
            Database
            <select
              value={selectedSchema}
              onChange={(e) => {
                const newSch = e.target.value;
                setSelectedSchema(newSch);
                const firstTable = hierarchy[selectedCatalog]?.[newSch]?.[0]?.full;
                if (firstTable) onTableChange(firstTable);
              }}
              className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm text-fg focus:outline-none focus:border-accent"
            >
              <option value="" disabled>Select Database</option>
              {availableSchemas.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        )}

        <label className="text-xs text-fg-subtle">
          Table
          <select
            value={tableName ?? ""}
            onChange={(e) => onTableChange(e.target.value)}
            className="mt-1 w-full px-3 py-2 rounded-lg bg-surface-overlay border border-border-strong text-sm text-fg focus:outline-none focus:border-accent"
          >
            <option value="" disabled>Select Table</option>
            {availableTables.map(t => <option key={t.full} value={t.full}>{t.display}</option>)}
          </select>
        </label>
      </div>

      <div>
        <div className="text-xs text-fg-subtle mb-1.5">Dimensions (group by)</div>
        <div className="flex flex-wrap gap-1.5">
          {columns.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => toggleDimension(c.column_name)}
              className={classNames(
                "px-2 py-1 text-[11px] rounded-lg border",
                dimensions.includes(c.column_name)
                  ? "bg-info/15 border-info/30 text-info"
                  : "border-border-strong text-fg-subtle hover:bg-surface-overlay",
              )}
            >
              {c.column_name}
            </button>
          ))}
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs text-fg-subtle">Measures</span>
          <button
            type="button"
            onClick={addMeasure}
            className="text-[11px] text-info hover:text-info"
          >
            + Add measure
          </button>
        </div>
        <div className="flex flex-col gap-2">
          {measures.map((m, i) => (
            <div key={i} className="flex items-center gap-2">
              <select
                value={m.aggregation}
                onChange={(e) => updateMeasure(i, { aggregation: e.target.value as Aggregation })}
                className="px-2 py-1.5 text-xs rounded-lg bg-surface-overlay border border-border-strong text-fg-muted"
              >
                {AGGREGATIONS.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
              <select
                value={m.field}
                onChange={(e) => updateMeasure(i, { field: e.target.value })}
                className="flex-1 px-2 py-1.5 text-xs rounded-lg bg-surface-overlay border border-border-strong text-fg-muted"
              >
                {columns.map((c) => <option key={c.id} value={c.column_name}>{c.column_name}</option>)}
              </select>
              <button
                type="button"
                onClick={() => removeMeasure(i)}
                aria-label="Remove measure"
                className="text-fg-subtle hover:text-danger text-xs px-1"
              >
                ✕
              </button>
            </div>
          ))}
          {measures.length === 0 && (
            <p className="text-[11px] text-fg-subtle">No measures yet — add one to aggregate values.</p>
          )}
        </div>
      </div>
    </div>
  );
}
