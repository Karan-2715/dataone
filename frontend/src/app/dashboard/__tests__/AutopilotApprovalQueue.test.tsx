import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ApprovalQueue from "../autopilot/components/ApprovalQueue";
import type { Recommendation } from "../autopilot/lib/types";

function rec(overrides: Partial<Recommendation> = {}): Recommendation {
  return {
    id: 7,
    action_type: "connector_health_check",
    payload: { connection_id: 3 },
    subject: "connection:3",
    rationale: {
      summary: "Connection 'CRM' is down; re-test to confirm recovery.",
      evidence: ["health_status=down", "last_test_error=connection refused"],
      trigger: { kind: "connector_health" },
    },
    confidence: 90,
    risk: "low",
    reversible: true,
    reversibility_note: "Read-only probe",
    status: "pending",
    created_by: "autopilot-engine",
    created_at: "2026-07-08T10:00:00Z",
    ...overrides,
  };
}

const noop = {
  onStatusFilter: () => {},
  onApprove: () => {},
  onReject: () => {},
  onModify: () => {},
  onEvaluate: () => {},
  evaluating: false,
  busyId: null,
  statusFilter: "pending",
};

describe("ApprovalQueue", () => {
  it("renders rationale, confidence, risk and reversibility for each item", () => {
    render(<ApprovalQueue items={[rec()]} role="viewer" {...noop} />);
    expect(
      screen.getByText(/Connection 'CRM' is down/),
    ).toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument();
    expect(screen.getByText("low risk")).toBeInTheDocument();
    expect(screen.getByText("reversible")).toBeInTheDocument();
    expect(screen.getByText(/Evidence \(2\)/)).toBeInTheDocument();
  });

  it("hides approve/reject/modify for non-admin roles", () => {
    render(<ApprovalQueue items={[rec()]} role="analyst" {...noop} />);
    expect(screen.queryByText("Approve")).not.toBeInTheDocument();
    expect(screen.queryByText("Reject")).not.toBeInTheDocument();
  });

  it("shows approve/reject/modify only for admin on pending items", () => {
    const onApprove = vi.fn();
    render(
      <ApprovalQueue
        items={[rec()]}
        role="admin"
        {...noop}
        onApprove={onApprove}
      />,
    );
    fireEvent.click(screen.getByText("Approve"));
    expect(onApprove).toHaveBeenCalledWith(7);
  });

  it("hides decision buttons on already-decided items even for admin", () => {
    render(
      <ApprovalQueue items={[rec({ status: "executed" })]} role="admin" {...noop} />,
    );
    expect(screen.queryByText("Approve")).not.toBeInTheDocument();
  });

  it("modify validates JSON before submitting", () => {
    const onModify = vi.fn();
    render(
      <ApprovalQueue items={[rec()]} role="admin" {...noop} onModify={onModify} />,
    );
    fireEvent.click(screen.getByText("Modify"));
    const textarea = screen.getByLabelText("Modify payload JSON");
    fireEvent.change(textarea, { target: { value: "{not json" } });
    fireEvent.click(screen.getByText("Save payload"));
    expect(onModify).not.toHaveBeenCalled();
    expect(screen.getByText("Invalid JSON")).toBeInTheDocument();

    fireEvent.change(textarea, { target: { value: '{"connection_id": 4}' } });
    fireEvent.click(screen.getByText("Save payload"));
    expect(onModify).toHaveBeenCalledWith(7, { connection_id: 4 });
  });

  it("offers evaluate-now to analysts but not viewers", () => {
    const { rerender } = render(
      <ApprovalQueue items={[]} role="analyst" {...noop} />,
    );
    expect(screen.getByText(/Evaluate triggers now/)).toBeInTheDocument();
    rerender(<ApprovalQueue items={[]} role="viewer" {...noop} />);
    expect(screen.queryByText(/Evaluate triggers now/)).not.toBeInTheDocument();
  });
});
