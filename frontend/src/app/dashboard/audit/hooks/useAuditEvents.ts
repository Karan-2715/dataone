"use client";
import { api } from "@/lib/api";
import { useWidgetData } from "../../hooks/useWidgetData";
import { AuditFilters, AuditSearchResponse, SortBy, SortOrder } from "../lib/types";

export function buildAuditQuery(
  filters: AuditFilters | undefined,
  page: number,
  pageSize: number,
  sortBy?: SortBy,
  sortOrder?: SortOrder,
  correlationId?: string,
): string {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (correlationId) {
    params.set("correlation_id", correlationId);
  } else if (filters) {
    if (filters.actor) params.set("actor", filters.actor);
    if (filters.module) params.set("module", filters.module);
    if (filters.event_type) params.set("event_type", filters.event_type);
    if (filters.outcome) params.set("outcome", filters.outcome);
    if (filters.date_from) params.set("date_from", filters.date_from);
    if (filters.date_to) params.set("date_to", filters.date_to);
    if (filters.search) params.set("search", filters.search);
  }
  if (sortBy) params.set("sort_by", sortBy);
  if (sortOrder) params.set("sort_order", sortOrder);
  return params.toString();
}

/** Powers the main event table (dashboard/audit/page.tsx). */
export function useAuditEvents(
  filters: AuditFilters,
  page: number,
  pageSize: number,
  sortBy: SortBy,
  sortOrder: SortOrder,
) {
  return useWidgetData<AuditSearchResponse>(
    (signal) =>
      api.get<AuditSearchResponse>(
        `/api/v1/audit/events?${buildAuditQuery(filters, page, pageSize, sortBy, sortOrder)}`,
        { signal },
      ),
    [
      filters.actor, filters.module, filters.event_type, filters.outcome,
      filters.date_from, filters.date_to, filters.search,
      page, pageSize, sortBy, sortOrder,
    ],
  );
}

/** Powers the correlation trace timeline (always sequence-ordered by the API default). */
export function useCorrelationTrace(correlationId: string | null) {
  return useWidgetData<AuditSearchResponse>(
    (signal) => {
      if (!correlationId) {
        return Promise.resolve<AuditSearchResponse>({
          events: [], total: 0, page: 1, page_size: 200, has_more: false, facets: null,
        });
      }
      return api.get<AuditSearchResponse>(
        `/api/v1/audit/events?${buildAuditQuery(undefined, 1, 200, undefined, undefined, correlationId)}`,
        { signal },
      );
    },
    [correlationId],
  );
}
