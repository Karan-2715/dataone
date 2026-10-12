"use client";
import { AuditEvent, SortBy, SortOrder } from "../lib/types";

const STATUS_STYLES: Record<string, string> = {
  success: "bg-success/10 text-success border-success/20",
  failure: "bg-danger/10 text-danger border-danger/20",
  warning: "bg-warning/10 text-warning border-warning/20",
};

const COLUMNS: { key: SortBy; label: string }[] = [
  { key: "created_at", label: "Timestamp" },
  { key: "event_type", label: "Event" },
  { key: "module", label: "Module" },
  { key: "actor", label: "Actor" },
  { key: "outcome", label: "Outcome" },
];

export default function EventTable({
  events,
  isLoading,
  sortBy,
  sortOrder,
  onSort,
  onSelect,
  selectedId,
}: {
  events: AuditEvent[];
  isLoading: boolean;
  sortBy: SortBy;
  sortOrder: SortOrder;
  onSort: (col: SortBy) => void;
  onSelect: (event: AuditEvent) => void;
  selectedId: number | null;
}) {
  if (isLoading) {
    return <div className="p-8 text-center text-fg-subtle text-sm">Loading audit events…</div>;
  }
  if (events.length === 0) {
    return (
      <div className="p-8 text-center text-fg-subtle text-sm">
        No audit events match the current filters.
      </div>
    );
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-border text-fg-subtle text-left text-xs">
          {COLUMNS.map((col) => (
            <th key={col.key} className="p-4">
              <button
                onClick={() => onSort(col.key)}
                className="flex items-center gap-1 hover:text-fg-muted"
              >
                {col.label}
                {sortBy === col.key && <span>{sortOrder === "asc" ? "▲" : "▼"}</span>}
              </button>
            </th>
          ))}
          <th className="p-4">Target</th>
          <th className="p-4">Summary</th>
        </tr>
      </thead>
      <tbody>
        {events.map((ev) => (
          <tr
            key={ev.id}
            onClick={() => onSelect(ev)}
            className={`border-b border-border/50 hover:bg-surface-overlay cursor-pointer transition-colors ${
              selectedId === ev.id ? "bg-surface-overlay" : ""
            }`}
          >
            <td className="p-4 text-fg-subtle font-mono text-xs whitespace-nowrap">
              {new Date(ev.created_at).toLocaleString()}
            </td>
            <td className="p-4 text-fg-muted font-medium">{ev.event_type}</td>
            <td className="p-4 text-fg-subtle">{ev.module ?? "—"}</td>
            <td className="p-4 text-fg-subtle text-xs">{ev.actor}</td>
            <td className="p-4">
              <span className={`px-2 py-0.5 rounded-full text-xs border ${STATUS_STYLES[ev.outcome] ?? ""}`}>
                {ev.outcome}
              </span>
            </td>
            <td className="p-4 text-fg-subtle text-xs">
              {ev.target_name ?? ev.target_type ?? "—"}
            </td>
            <td className="p-4 text-fg-subtle text-xs max-w-xs truncate">{ev.summary ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
