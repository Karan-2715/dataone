"use client";

import { Card } from "../../components";
import type { AISuggestion } from "../lib/types";

interface ConfidenceHeatmapProps {
  suggestions: AISuggestion[];
}

function confidencePercent(value: number): number {
  return Math.round(Math.max(0, Math.min(100, value <= 1 ? value * 100 : value)));
}

function cellStyle(value: number | null): string {
  if (value == null) return "bg-surface-overlay text-fg-subtle";
  if (value >= 80) return "bg-success/25 text-success";
  if (value >= 60) return "bg-warning/25 text-warning";
  return "bg-danger/20 text-danger";
}

export function ConfidenceHeatmap({ suggestions }: ConfidenceHeatmapProps) {
  if (suggestions.length === 0) return null;

  const sources = [...new Set(suggestions.map((item) => item.source_table))].sort();
  const targets = [...new Set(suggestions.map((item) => item.target_table))].sort();
  const buckets = new Map<string, number[]>();
  for (const suggestion of suggestions) {
    const key = `${suggestion.source_table}\u0000${suggestion.target_table}`;
    const bucket = buckets.get(key) ?? [];
    bucket.push(confidencePercent(suggestion.confidence));
    buckets.set(key, bucket);
  }

  return (
    <Card variant="default" padding="sm" className="mx-5 mb-3 overflow-x-auto">
      <div className="mb-2">
        <h4 className="text-xs font-semibold text-fg-muted">Table confidence heatmap</h4>
        <p className="text-[10px] text-fg-subtle">Average confidence across discovered field matches</p>
      </div>
      <table className="w-full border-separate border-spacing-1 text-[10px]" aria-label="Table confidence heatmap">
        <thead>
          <tr>
            <th scope="col" className="p-1 text-left font-medium text-fg-subtle">Source ↓ / Target →</th>
            {targets.map((target) => (
              <th key={target} scope="col" className="max-w-28 truncate p-1 text-center font-medium text-fg-subtle" title={target}>
                {target}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sources.map((source) => (
            <tr key={source}>
              <th scope="row" className="max-w-28 truncate p-1 text-left font-medium text-fg-subtle" title={source}>
                {source}
              </th>
              {targets.map((target) => {
                const values = buckets.get(`${source}\u0000${target}`);
                const average = values?.length
                  ? Math.round(values.reduce((sum, value) => sum + value, 0) / values.length)
                  : null;
                return (
                  <td key={target} className="p-0.5 text-center">
                    <span
                      className={`block rounded px-2 py-1.5 font-semibold tabular-nums ${cellStyle(average)}`}
                      title={average == null ? "No discovered matches" : `${values!.length} match${values!.length === 1 ? "" : "es"}, ${average}% average confidence`}
                    >
                      {average == null ? "—" : `${average}%`}
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
