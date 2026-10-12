import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "../page";
import type { DashboardSummary } from "../types";

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { get: getMock, post: postMock },
}));

function summary(overrides: Partial<DashboardSummary> = {}): DashboardSummary {
  return {
    kpis: [
      {
        label: "Connected Sources", value: 3, link_url: "/dashboard/connectors",
        module: "connectors", status: "loaded",
      },
    ],
    feed: [],
    range: "7d",
    generated_at: "2026-07-09T00:00:00Z",
    available_views: ["combined", "executive", "operational"],
    ...overrides,
  };
}

// Route every GET by path prefix so each widget's fetch resolves
// independently, matching the real per-widget isolation (FR6).
function routeGet(handlers: {
  summary?: DashboardSummary | Error;
  drift?: unknown[] | Error;
  connectors?: unknown[] | Error;
}) {
  getMock.mockImplementation(async (path: string) => {
    const resolve = (v: unknown, fallback: unknown) => {
      const value = v ?? fallback;
      if (value instanceof Error) throw value;
      return value;
    };
    if (path.startsWith("/api/v1/dashboard/summary")) {
      return resolve(handlers.summary, summary());
    }
    if (path.startsWith("/api/v1/audit/")) {
      return resolve(handlers.drift, []);
    }
    if (path.startsWith("/api/v1/connectors/")) {
      return resolve(handlers.connectors, []);
    }
    throw new Error(`unexpected path in test: ${path}`);
  });
}

describe("DashboardPage", () => {
  beforeEach(() => {
    localStorage.clear();
    getMock.mockReset();
    postMock.mockReset();
    postMock.mockResolvedValue({ status: "connected" });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders migration readiness in the executive KPI row", async () => {
    routeGet({
      summary: summary({
        kpis: [{
          label: "Migration Readiness",
          value: 82,
          subtitle: "Mapping 80% · Drift 80% · Pipelines 90%",
          link_url: "/dashboard/schema-mapper",
          module: "mappings",
          status: "loaded",
        }],
      }),
    });
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText("Migration Readiness")).toBeInTheDocument());
    expect(screen.getByText("Mapping 80% · Drift 80% · Pipelines 90%")).toBeInTheDocument();
  });

  it("switches between backend-authorized executive and operational views", async () => {
    routeGet({
      summary: summary({
        kpis: [
          { label: "Total Tables", value: 12, link_url: "/dashboard/schema", module: "schema_intel", status: "loaded" },
          { label: "Connected Sources", value: 3, link_url: "/dashboard/connectors", module: "connectors", status: "loaded" },
        ],
      }),
    });
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText("Total Tables")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("radio", { name: "Operational" }));
    expect(screen.queryByText("Total Tables")).not.toBeInTheDocument();
    expect(screen.getByText("Connected Sources")).toBeInTheDocument();
    expect(localStorage.getItem("dashboard_view")).toBe("operational");
  });

  it("does not render a view toggle when the backend only authorizes operational", async () => {
    routeGet({ summary: summary({ available_views: ["operational"] }) });
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText("Connected Sources")).toBeInTheDocument());
    expect(screen.queryByRole("radiogroup", { name: "Dashboard view" })).not.toBeInTheDocument();
  });

  it("renders skeleton tiles before the summary resolves, then real tiles after", async () => {
    let resolveSummary!: (v: DashboardSummary) => void;
    getMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/v1/dashboard/summary")) {
        return new Promise((r) => (resolveSummary = r));
      }
      return [];
    });

    render(<DashboardPage />);
    // 8 skeleton tiles render immediately (SKELETON_TILE_COUNT) so the grid
    // doesn't reflow once real data lands.
    expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThanOrEqual(8);
    expect(screen.queryByText("Connected Sources")).not.toBeInTheDocument();

    await act(async () => resolveSummary(summary()));
    await waitFor(() => expect(screen.getByText("Connected Sources")).toBeInTheDocument());
  });

  it("shows an error banner with Retry when the summary fetch fails, and Retry re-fetches", async () => {
    let shouldFail = true;
    getMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/v1/dashboard/summary")) {
        if (shouldFail) throw new Error("summary blew up");
        return summary();
      }
      return [];
    });

    render(<DashboardPage />);
    // The same summary error also drives ActivityFeed's error state (page.tsx
    // passes it errorMessage too), so "summary blew up" legitimately appears
    // twice — assert on the KPI banner's unique heading text instead.
    await waitFor(() =>
      expect(screen.getByText(/Failed to load dashboard summary/)).toBeInTheDocument(),
    );
    expect(screen.getAllByText(/summary blew up/).length).toBeGreaterThan(0);

    shouldFail = false;
    // Both the KPI banner and ActivityFeed render their own Retry button off
    // the same summary.refetch (page.tsx wires both to it) — either works.
    await act(async () => {
      fireEvent.click(screen.getAllByText("Retry")[0]);
    });
    await waitFor(() => expect(screen.getByText("Connected Sources")).toBeInTheDocument());
  });

  it("hides the drift banner when there is no drift", async () => {
    routeGet({ drift: [] });
    render(<DashboardPage />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(screen.queryByText(/Schema Drift Detected/)).not.toBeInTheDocument();
  });

  it("shows the drift banner with alert rows when drift events exist", async () => {
    routeGet({
      drift: [{
        id: 1, connection_name: "CRM", created_at: "2026-07-09T00:00:00Z", payload: null,
      }],
    });
    render(<DashboardPage />);
    await waitFor(() =>
      expect(screen.getByText(/Schema Drift Detected/)).toBeInTheDocument(),
    );
    expect(screen.getByText("CRM")).toBeInTheDocument();
  });

  it("does not show the drift banner when the drift widget itself errored", async () => {
    routeGet({ drift: new Error("audit unavailable") });
    render(<DashboardPage />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(screen.queryByText("Schema Drift Detected")).not.toBeInTheDocument();
  });

  it("persists the selected time range and re-fetches the summary with it", async () => {
    routeGet({});
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText("Connected Sources")).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByRole("radio", { name: "24h" }));
    });

    expect(localStorage.getItem("dashboard_time_range")).toBe("24h");
    await waitFor(() =>
      expect(getMock).toHaveBeenCalledWith(
        "/api/v1/dashboard/summary?range=24h",
        expect.anything(),
      ),
    );
  });

  it("reads a valid persisted range on mount instead of defaulting to 7d", async () => {
    localStorage.setItem("dashboard_time_range", "30d");
    routeGet({});
    render(<DashboardPage />);
    await waitFor(() =>
      expect(getMock).toHaveBeenCalledWith(
        "/api/v1/dashboard/summary?range=30d",
        expect.anything(),
      ),
    );
  });

  it("probes each connector and renders its live connected/failed status", async () => {
    routeGet({
      connectors: [
        { id: 1, name: "Prod DB", type: "postgres" },
        { id: 2, name: "Legacy DB", type: "oracle" },
      ],
    });
    postMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/connectors/1/test") return { status: "connected" };
      if (path === "/api/v1/connectors/2/test") return { status: "down" };
      throw new Error(`unexpected post: ${path}`);
    });

    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText("Prod DB")).toBeInTheDocument());

    await waitFor(() => expect(screen.getByText("● Connected")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("● Failed")).toBeInTheDocument());
    expect(postMock).toHaveBeenCalledWith("/api/v1/connectors/1/test", {});
    expect(postMock).toHaveBeenCalledWith("/api/v1/connectors/2/test", {});
  });

  it("shows an empty state with an add-connection link when there are no connectors", async () => {
    routeGet({ connectors: [] });
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText("No connections yet.")).toBeInTheDocument());
  });
});
