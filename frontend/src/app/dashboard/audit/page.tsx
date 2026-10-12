"use client";
import { useState } from "react";
import { useAuditEvents } from "./hooks/useAuditEvents";
import FilterBar from "./components/FilterBar";
import EventTable from "./components/EventTable";
import EventDetail from "./components/EventDetail";
import ExportButton from "./components/ExportButton";
import { AuditEvent, AuditFilters, EMPTY_FILTERS, SortBy, SortOrder } from "./lib/types";
import { ErrorState, WorkspaceHeader } from "../components";

const PAGE_SIZE = 50;

export default function AuditPage() {
  const [filters, setFilters] = useState<AuditFilters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState<SortBy>("created_at");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [selected, setSelected] = useState<AuditEvent | null>(null);

  const { data, isLoading, isError, errorMessage, refetch } = useAuditEvents(
    filters, page, PAGE_SIZE, sortBy, sortOrder,
  );

  const handleFilters = (next: AuditFilters) => {
    setFilters(next);
    setPage(1);
  };

  const handleSort = (col: SortBy) => {
    if (col === sortBy) {
      setSortOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(col);
      setSortOrder("desc");
    }
  };

  const events = data?.events ?? [];
  const total = data?.total ?? 0;
  const hasMore = data?.has_more ?? false;

  return (
    <div className="workspace-page flex min-h-full flex-col gap-4 p-4 md:p-6">
      <WorkspaceHeader eyebrow="Governance & Compliance" title="Audit Trail" description="Search immutable platform events, inspect before/after evidence, follow correlation traces, and export filtered records." actions={<ExportButton filters={filters} />} />

      <FilterBar
        filters={filters}
        onChange={handleFilters}
        facets={data?.facets ?? null}
        onRefresh={refetch}
      />

      {isError && (
        <ErrorState title="Audit events could not be loaded" message={errorMessage ?? undefined} onRetry={refetch} />
      )}

      <div className={selected ? "grid gap-4 xl:grid-cols-[minmax(0,1fr)_26rem]" : "grid gap-4"}>
        <div className="overflow-x-auto rounded-2xl border border-glass-border bg-glass-bg shadow-[var(--glass-shadow)]">
          <EventTable
            events={events}
            isLoading={isLoading}
            sortBy={sortBy}
            sortOrder={sortOrder}
            onSort={handleSort}
            onSelect={setSelected}
            selectedId={selected?.id ?? null}
          />
        </div>
        {selected && (
          <EventDetail event={selected} onClose={() => setSelected(null)} onSelectEvent={setSelected} />
        )}
      </div>

      <div className="flex items-center justify-between">
        <span className="text-xs text-fg-subtle">{total} event{total === 1 ? "" : "s"}</span>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1.5 text-xs bg-surface-overlay hover:bg-surface-overlay text-fg-muted rounded-lg disabled:opacity-40"
          >
            Previous
          </button>
          <span className="text-xs text-fg-subtle">Page {page}</span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={!hasMore}
            className="px-3 py-1.5 text-xs bg-surface-overlay hover:bg-surface-overlay text-fg-muted rounded-lg disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
