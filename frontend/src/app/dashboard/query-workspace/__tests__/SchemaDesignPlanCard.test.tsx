import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SchemaDesignPlanCard from "../components/SchemaDesignPlanCard";
import type { SchemaDesignPlan } from "../../askdata/lib/types";

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { get: getMock, post: postMock },
  ApiError: class ApiError extends Error {
    constructor(public status: number, message: string) { super(message); }
  },
}));

function plan(overrides: Partial<SchemaDesignPlan> = {}): SchemaDesignPlan {
  return {
    id: 7,
    session_id: "s1",
    question: "create retail analytics schemas",
    source_connection_id: 1,
    target_connection_id: null,
    status: "ready",
    domain_template: "retail_analytics",
    dialect: "sqlite",
    proposed_tables: [{
      name: "dim_customers",
      columns: [
        { name: "customer_key", type: "INTEGER", nullable: false, primary_key: true, source_refs: [] },
        { name: "email", type: "TEXT", nullable: true, primary_key: false,
          source_refs: [{ table: "customers", column: "email" }] },
      ],
    }],
    dq_rules: [{
      rule: "unique", target_table: "dim_customers", target_column: "email",
      justification: "appears unique in the profiled sample (99.20%)", confidence: 0.99,
    }],
    transformations: [{
      target_table: "dim_customers", target_column: "email",
      sources: [{ table: "customers", column: "email" }],
      transformation: { kind: "direct" }, note: null,
    }],
    generated_ddl: [{
      table: "dim_customers", mode: "create",
      statements: ["CREATE TABLE dim_customers (customer_key INTEGER PRIMARY KEY, email TEXT)"],
    }],
    confidence_notes: ["LLM unreachable — using deterministic proposal"],
    apply_results: null,
    created_mapping_id: null,
    error: null,
    created_by: "admin@test",
    created_at: "2026-07-14T00:00:00Z",
    decided_by: null,
    decided_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  getMock.mockReset();
  postMock.mockReset();
});

describe("SchemaDesignPlanCard", () => {
  it("renders a ready plan with all sections and enabled actions", async () => {
    getMock.mockResolvedValue(plan());
    render(<SchemaDesignPlanCard planId={7} />);

    await waitFor(() => expect(screen.getByText(/Schema Design Plan #7/)).toBeInTheDocument());
    expect(screen.getByText("Ready for review")).toBeInTheDocument();
    expect(screen.getByText("dim_customers")).toBeInTheDocument();
    expect(screen.getByText(/Approve & Create/)).not.toBeDisabled();
    expect(screen.getByText("Reject")).not.toBeDisabled();
  });

  it("disables actions and shows progress while generating", async () => {
    getMock.mockResolvedValue(plan({
      status: "generating", proposed_tables: null, dq_rules: null,
      transformations: null, generated_ddl: null, confidence_notes: null,
    }));
    render(<SchemaDesignPlanCard planId={7} />);

    await waitFor(() => expect(screen.getByText("Generating plan…")).toBeInTheDocument());
    expect(screen.getByText(/Approve & Create/)).toBeDisabled();
    expect(screen.getByText("Reject")).toBeDisabled();
  });

  it("polls while generating and stops once ready", async () => {
    getMock
      .mockResolvedValueOnce(plan({ status: "generating" }))
      .mockResolvedValue(plan({ status: "ready" }));
    render(<SchemaDesignPlanCard planId={7} />);

    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(2600);
    await waitFor(() => expect(screen.getByText("Ready for review")).toBeInTheDocument());
    const calls = getMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(6000);
    expect(getMock.mock.calls.length).toBe(calls); // polling stopped
  });

  it("approve is two-step and posts to the approve endpoint", async () => {
    getMock.mockResolvedValue(plan());
    postMock.mockResolvedValue(plan({
      status: "applied",
      apply_results: [{ table: "dim_customers", status: "applied", statements_executed: 1 }],
    }));
    render(<SchemaDesignPlanCard planId={7} />);

    await waitFor(() => expect(screen.getByText(/Approve & Create/)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Approve & Create/));
    expect(postMock).not.toHaveBeenCalled(); // first click only arms the confirm
    fireEvent.click(screen.getByText(/Confirm: create\/alter/));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/api/v1/agentic-dba/plans/7/approve", {}));
    await waitFor(() => expect(screen.getByText("Applied")).toBeInTheDocument());
    expect(screen.getByText(/dim_customers — applied/)).toBeInTheDocument();
  });

  it("reject posts to the reject endpoint", async () => {
    getMock.mockResolvedValue(plan());
    postMock.mockResolvedValue(plan({ status: "rejected" }));
    render(<SchemaDesignPlanCard planId={7} />);

    await waitFor(() => expect(screen.getByText("Reject")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Reject"));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/api/v1/agentic-dba/plans/7/reject", {}));
    await waitFor(() => expect(screen.getByText("Rejected")).toBeInTheDocument());
  });

  it("shows per-object results including failures for partially applied plans", async () => {
    getMock.mockResolvedValue(plan({
      status: "partially_applied",
      apply_results: [
        { table: "dim_customers", status: "applied", statements_executed: 1 },
        { table: "fact_orders", status: "failed", error: "syntax error" },
        { table: "dim_products", status: "skipped" },
      ],
    }));
    render(<SchemaDesignPlanCard planId={7} />);

    await waitFor(() => expect(screen.getByText("Partially applied")).toBeInTheDocument());
    expect(screen.getByText(/fact_orders — failed/)).toBeInTheDocument();
    expect(screen.getByText(/syntax error/)).toBeInTheDocument();
    expect(screen.getByText(/dim_products — skipped/)).toBeInTheDocument();
    expect(screen.getByText(/Approve & Create/)).toBeDisabled();
  });

  it("surfaces an approve failure without losing the plan", async () => {
    getMock.mockResolvedValue(plan());
    postMock.mockRejectedValue(new Error("boom"));
    render(<SchemaDesignPlanCard planId={7} />);

    await waitFor(() => expect(screen.getByText(/Approve & Create/)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Approve & Create/));
    fireEvent.click(screen.getByText(/Confirm: create\/alter/));
    await waitFor(() =>
      expect(screen.getByText(/Could not approve the plan/)).toBeInTheDocument());
    expect(screen.getByText("dim_customers")).toBeInTheDocument();
  });
});
