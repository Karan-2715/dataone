"use client";
import { AuditFacets, AuditFilters } from "../lib/types";

export default function FilterBar({
  filters,
  onChange,
  facets,
  onRefresh,
}: {
  filters: AuditFilters;
  onChange: (next: AuditFilters) => void;
  facets: AuditFacets | null;
  onRefresh: () => void;
}) {
  const set = (patch: Partial<AuditFilters>) => onChange({ ...filters, ...patch });

  return (
    <div className="glass flex flex-wrap items-center gap-3 rounded-xl p-3" role="search" aria-label="Filter audit events">
      <input
        type="text"
        placeholder="Search summary / event type / actor…"
        value={filters.search}
        onChange={(e) => set({ search: e.target.value })}
        aria-label="Search audit events"
        className="bg-surface-overlay border border-border-strong text-fg-muted text-sm rounded-lg px-3 py-2 min-w-[220px]"
      />
      <select
        aria-label="Filter by module"
        value={filters.module}
        onChange={(e) => set({ module: e.target.value })}
        className="bg-surface-overlay border border-border-strong text-fg-muted text-sm rounded-lg px-3 py-2"
      >
        <option value="">All Modules</option>
        {Object.keys(facets?.modules ?? {}).map((m) => (
          <option key={m} value={m}>{m} ({facets?.modules[m]})</option>
        ))}
      </select>
      <select
        aria-label="Filter by event type"
        value={filters.event_type}
        onChange={(e) => set({ event_type: e.target.value })}
        className="bg-surface-overlay border border-border-strong text-fg-muted text-sm rounded-lg px-3 py-2"
      >
        <option value="">All Event Types</option>
        {Object.keys(facets?.event_types ?? {}).map((t) => (
          <option key={t} value={t}>{t} ({facets?.event_types[t]})</option>
        ))}
      </select>
      <select
        aria-label="Filter by outcome"
        value={filters.outcome}
        onChange={(e) => set({ outcome: e.target.value })}
        className="bg-surface-overlay border border-border-strong text-fg-muted text-sm rounded-lg px-3 py-2"
      >
        <option value="">All Outcomes</option>
        <option value="success">Success</option>
        <option value="failure">Failure</option>
        <option value="warning">Warning</option>
      </select>
      <input
        type="text"
        placeholder="Actor"
        value={filters.actor}
        onChange={(e) => set({ actor: e.target.value })}
        aria-label="Filter by actor"
        className="bg-surface-overlay border border-border-strong text-fg-muted text-sm rounded-lg px-3 py-2 w-36"
      />
      <input
        type="datetime-local"
        aria-label="From date"
        value={filters.date_from}
        onChange={(e) => set({ date_from: e.target.value })}
        className="bg-surface-overlay border border-border-strong text-fg-muted text-sm rounded-lg px-3 py-2"
      />
      <input
        type="datetime-local"
        aria-label="To date"
        value={filters.date_to}
        onChange={(e) => set({ date_to: e.target.value })}
        className="bg-surface-overlay border border-border-strong text-fg-muted text-sm rounded-lg px-3 py-2"
      />
      <button
        type="button"
        onClick={onRefresh}
        className="workspace-primary-action min-h-0 rounded-lg px-4 py-2 text-sm"
      >
        Refresh
      </button>
    </div>
  );
}
