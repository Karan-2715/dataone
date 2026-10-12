import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AuditPage from "../page";
import type { AuditEvent, AuditSearchResponse } from "../lib/types";

const { getMock, downloadMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  downloadMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { get: getMock, download: downloadMock },
}));

function makeEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: 1,
    event_type: "connector.created",
    actor: "alice@x.com",
    module: "connectors",
    target_type: "connection",
    target_id: "42",
    target_name: "prod-db",
    before_summary: null,
    after_summary: { name: "prod-db" },
    correlation_id: null,
    outcome: "success",
    summary: "Created connection prod-db",
    duration_ms: 120,
    metadata: { rows: 3 },
    connection_id: null,
    connection_name: null,
    payload: null,
    status: "success",
    event_hash: "abc123",
    sequence: 1,
    created_at: "2026-07-09T12:00:00Z",
    ...overrides,
  };
}

function searchResponse(events: AuditEvent[], overrides: Partial<AuditSearchResponse> = {}): AuditSearchResponse {
  return {
    events,
    total: events.length,
    page: 1,
    page_size: 50,
    has_more: false,
    facets: {
      modules: { connectors: 1 },
      event_types: { "connector.created": 1 },
      outcomes: { success: 1 },
      actors: { "alice@x.com": 1 },
      date_range: null,
    },
    ...overrides,
  };
}

describe("AuditPage", () => {
  beforeEach(() => {
    getMock.mockReset();
    downloadMock.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders fetched events in the table", async () => {
    getMock.mockResolvedValue(searchResponse([makeEvent()]));
    render(<AuditPage />);

    await waitFor(() => expect(screen.getByText("connector.created")).toBeInTheDocument());
    expect(screen.getByText("prod-db")).toBeInTheDocument();
    expect(screen.getByText("alice@x.com")).toBeInTheDocument();
  });

  it("shows the empty state when no events match", async () => {
    getMock.mockResolvedValue(searchResponse([]));
    render(<AuditPage />);

    await waitFor(() =>
      expect(screen.getByText(/No audit events match the current filters/)).toBeInTheDocument(),
    );
  });

  it("refetches with the module filter applied", async () => {
    getMock.mockResolvedValue(searchResponse([makeEvent()]));
    render(<AuditPage />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());

    getMock.mockClear();
    const moduleSelect = screen.getByDisplayValue("All Modules");
    fireEvent.change(moduleSelect, { target: { value: "connectors" } });

    await waitFor(() => {
      const lastCall = getMock.mock.calls.at(-1)?.[0] as string;
      expect(lastCall).toContain("module=connectors");
    });
  });

  it("opens the detail panel with full event fields on row click", async () => {
    getMock.mockResolvedValue(searchResponse([makeEvent()]));
    render(<AuditPage />);

    await waitFor(() => expect(screen.getByText("connector.created")).toBeInTheDocument());
    fireEvent.click(screen.getByText("connector.created"));

    await waitFor(() => expect(screen.getByTestId("event-detail")).toBeInTheDocument());
    const detail = screen.getByTestId("event-detail");
    expect(within(detail).getByText(/seq 1/)).toBeInTheDocument();
    expect(within(detail).getByText("Created connection prod-db")).toBeInTheDocument();
    expect(within(detail).getByText("prod-db")).toBeInTheDocument();
  });

  it("loads a correlation trace when the selected event has a correlation_id", async () => {
    const cid = "corr-123";
    const first = makeEvent({ id: 1, event_type: "pipeline.started", correlation_id: cid, sequence: 1 });
    const second = makeEvent({ id: 2, event_type: "pipeline.completed", correlation_id: cid, sequence: 2 });

    getMock.mockImplementation(async (path: string) => {
      if (path.includes("correlation_id=corr-123")) {
        return searchResponse([first, second]);
      }
      return searchResponse([first]);
    });

    render(<AuditPage />);
    await waitFor(() => expect(screen.getByText("pipeline.started")).toBeInTheDocument());
    fireEvent.click(screen.getByText("pipeline.started"));

    await waitFor(() => expect(screen.getByText("Correlation Trace")).toBeInTheDocument());
    await waitFor(() => {
      const trace = screen.getByText("Correlation Trace").closest("div")!.parentElement!;
      expect(within(trace).getByText("pipeline.completed")).toBeInTheDocument();
    });
  });

  it("triggers a CSV download via api.download when Export CSV is clicked", async () => {
    getMock.mockResolvedValue(searchResponse([makeEvent()]));
    downloadMock.mockResolvedValue({ blob: new Blob(["a,b\n1,2"]), filename: "audit_export_2026-07-09.csv" });
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    // jsdom doesn't implement these.
    (URL as unknown as { createObjectURL: typeof createObjectURL }).createObjectURL = createObjectURL;
    (URL as unknown as { revokeObjectURL: typeof revokeObjectURL }).revokeObjectURL = revokeObjectURL;

    render(<AuditPage />);
    await waitFor(() => expect(screen.getByText("connector.created")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Export CSV"));

    await waitFor(() => expect(downloadMock).toHaveBeenCalledTimes(1));
    const call = downloadMock.mock.calls[0][0] as string;
    expect(call).toContain("format=csv");
    expect(createObjectURL).toHaveBeenCalled();
  });
});
