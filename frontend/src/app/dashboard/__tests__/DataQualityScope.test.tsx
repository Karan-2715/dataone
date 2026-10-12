import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DataQualitySection } from "../components/DataQualitySection";
import type { UseWidgetDataResult } from "../hooks/useWidgetData";
import type { DQSummary } from "../types";

vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  LineChart: () => null, Line: () => null, XAxis: () => null, YAxis: () => null, Tooltip: () => null,
}));

const get = vi.fn();
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => get(...args), post: vi.fn() },
  ApiError: class ApiError extends Error {},
}));

const run = (over: Partial<NonNullable<DQSummary["latest"]>>) => ({
  run_id: "r", source: "databricks", catalog: "workspace", schema_name: null, table_count: 15, total_rows: 1000,
  overall_score: 92.2, completeness: 97.8, uniqueness: 100, validity: 99.8, freshness: 64.1,
  completed_at: new Date().toISOString(), ...over,
});
const row = (name: string) => ({
  table_name: name, row_count: 891, column_count: 12, completeness: 91.9, uniqueness: 100, validity: 100,
  freshness: 100, overall_score: 98, last_modified: null, issues: [],
});

const all: DQSummary = {
  ingest_configured: true, trend: [], latest: run({}), tables: [row("default.titanic")],
  scope: "all", scope_counts: { all: 15, files: 1, tables: 14 },
};
const files: DQSummary = {
  ...all, scope: "files", latest: run({ table_count: 1, overall_score: 98, completeness: 91.9, uniqueness: 100, validity: 100, freshness: 100 }),
};

const widget = (data: DQSummary): UseWidgetDataResult<DQSummary> =>
  ({ data, isLoading: false, isError: false, errorMessage: undefined, refetch: vi.fn() });

describe("DataQualitySection scope switch", () => {
  beforeEach(() => { get.mockReset(); window.localStorage.clear(); });

  it("lists the datasets by default and labels each scope with its dataset count", () => {
    render(<DataQualitySection widget={widget(all)} />);
    expect(screen.getByRole("radio", { name: /All datasets \(15\)/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Uploaded files \(1\)/ })).toBeInTheDocument();
    expect(screen.getByText("default.titanic")).toBeInTheDocument();
    expect(screen.queryByText("97.8%")).not.toBeInTheDocument(); // no averaged headline tiles any more
    expect(get).not.toHaveBeenCalled();
  });

  it("switching to Uploaded files fetches that scope (all datasets, not just the worst 8) and lists its rows", async () => {
    get.mockResolvedValue({ ...files, tables: [row("default.titanic")] });
    render(<DataQualitySection widget={widget({ ...all, tables: [row("default.titanic"), row("workspace.sales.orders")] })} />);
    fireEvent.click(screen.getByRole("radio", { name: /Uploaded files/ }));
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(await screen.findByText("91.9%")).toBeInTheDocument(); // titanic's completeness in the table
    expect(screen.queryByText("workspace.sales.orders")).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledWith(expect.stringMatching(/table_limit=100.*scope=files/));
    expect(window.localStorage.getItem("dataone.dq.scope")).toBe("files");
  });

  it("restores the last chosen scope", async () => {
    window.localStorage.setItem("dataone.dq.scope", "files");
    get.mockResolvedValue(files);
    render(<DataQualitySection widget={widget(all)} />);
    await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining("scope=files")));
    expect(await screen.findByRole("radio", { name: /Uploaded files/ })).toBeChecked();
  });

  it("shows an empty state when the remembered scope has no results", async () => {
    window.localStorage.setItem("dataone.dq.scope", "files");
    const none = { ...all, scope: "files", latest: null, tables: [], scope_counts: { all: 14, files: 0, tables: 14 } };
    get.mockResolvedValue(none);
    render(<DataQualitySection widget={widget(none)} />);
    expect(await screen.findByText(/No uploaded-file results yet/)).toBeInTheDocument();
  });

  it("hides the switch when there are no uploaded files to separate out", () => {
    render(<DataQualitySection widget={widget({ ...all, scope_counts: { all: 14, files: 0, tables: 14 } })} />);
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
  });
});
