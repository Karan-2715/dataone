import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DataQualitySection, formatScore, scoreTone } from "../components/DataQualitySection";
import type { UseWidgetDataResult } from "../hooks/useWidgetData";
import type { DQSummary } from "../types";

// recharts needs layout APIs jsdom lacks; the sparkline isn't under test here.
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  LineChart: () => null, Line: () => null, XAxis: () => null, YAxis: () => null, Tooltip: () => null,
}));

function widget(over: Partial<UseWidgetDataResult<DQSummary>> = {}): UseWidgetDataResult<DQSummary> {
  return { data: null, isLoading: false, isError: false, errorMessage: undefined, refetch: vi.fn(), ...over };
}

const summary: DQSummary = {
  ingest_configured: true,
  trend: [
    { completed_at: "2026-10-08T10:00:00Z", overall_score: 80 },
    { completed_at: "2026-10-09T10:00:00Z", overall_score: 87.9 },
  ],
  latest: {
    run_id: "r1", source: "databricks", catalog: "workspace", schema_name: "sales", table_count: 2,
    total_rows: 150, overall_score: 87.9, completeness: 95, uniqueness: 95, validity: null, freshness: 80,
    completed_at: new Date().toISOString(),
  },
  tables: [
    {
      table_name: "workspace.sales.customers", row_count: 50, column_count: 4, completeness: 100, uniqueness: 90,
      validity: null, freshness: 60, overall_score: 83.3, last_modified: null,
      issues: [{ column: "email", check: "valid_email", detail: "12% invalid" }],
    },
    {
      table_name: "workspace.sales.orders", row_count: 12345, column_count: 5, completeness: 90, uniqueness: 100,
      validity: 80, freshness: 100, overall_score: 92.5, last_modified: null, issues: [],
    },
  ],
};

describe("scoring helpers", () => {
  it("formats null as an em dash, never a number", () => {
    expect(formatScore(null)).toBe("—");
    expect(formatScore(87.94)).toBe("87.9%");
  });
  it("colours by threshold and greys out unmeasured", () => {
    expect(scoreTone(95).text).toContain("emerald");
    expect(scoreTone(75).text).toContain("amber");
    expect(scoreTone(40).text).toContain("red");
    expect(scoreTone(null).text).toContain("white/40");
  });
});

describe("DataQualitySection", () => {
  it("shows a loading state before first data", () => {
    render(<DataQualitySection widget={widget({ isLoading: true })} />);
    expect(screen.getByRole("status", { name: /loading data quality/i })).toBeInTheDocument();
  });

  it("shows the error and retries", () => {
    const refetch = vi.fn();
    render(<DataQualitySection widget={widget({ isError: true, errorMessage: "boom", refetch })} />);
    expect(screen.getByText(/boom/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("explains the empty state when no run has reported", () => {
    render(<DataQualitySection widget={widget({ data: { latest: null, tables: [], trend: [], ingest_configured: true } })} />);
    expect(screen.getByText("No data quality results yet")).toBeInTheDocument();
    expect(screen.getByText(/Install DataOne in your Databricks workspace/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open workspace setup" })).toHaveAttribute("href", "/dashboard/workspace-setup");
  });

  it("flags a backend without an ingest key", () => {
    render(<DataQualitySection widget={widget({ data: { latest: null, tables: [], trend: [], ingest_configured: false } })} />);
    expect(screen.getByText(/DQ_INGEST_KEY/)).toBeInTheDocument();
  });

  it("shows only the per-dataset table (no summary cards) with numeric scores and dashes for unmeasured values", () => {
    render(<DataQualitySection widget={widget({ data: summary })} />);
    // the averaged score ring and dimension tiles are gone
    expect(screen.queryByRole("img", { name: /Overall data quality/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Overall quality score")).not.toBeInTheDocument();
    expect(screen.queryByText("Not measured")).not.toBeInTheDocument();

    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Dataset", "Rows", "Completeness", "Uniqueness", "Validity", "Freshness", "Score"]);

    const rows = screen.getAllByRole("row");
    const customers = within(rows.find((r) => r.textContent?.includes("customers"))!);
    expect(customers.getByText("100.0%")).toBeInTheDocument(); // completeness
    expect(customers.getByText("90.0%")).toBeInTheDocument(); // uniqueness
    expect(customers.getByText("—")).toBeInTheDocument(); // validity not measured
    expect(customers.getByText("60.0%")).toBeInTheDocument(); // freshness
    expect(customers.getByText("83.3%")).toBeInTheDocument(); // score
    expect(customers.getByText(/email: 12% invalid/)).toBeInTheDocument();
    expect(screen.getByText("12,345")).toBeInTheDocument();
    // lowest-scoring dataset first, as returned by the API
    expect(rows[1].textContent).toContain("customers");
  });
});
