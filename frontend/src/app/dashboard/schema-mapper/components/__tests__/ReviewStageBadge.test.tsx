import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ReviewStageBadge from "../ReviewStageBadge";

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  ApiError: class ApiError extends Error {},
}));

describe("ReviewStageBadge (Enterprise v2, E13)", () => {
  beforeEach(() => {
    postMock.mockReset();
  });

  it("renders the current stage label", () => {
    render(<ReviewStageBadge mappingId={1} reviewStage="pending_review" role="admin" onTransitioned={vi.fn()} />);
    expect(screen.getByText("Pending Review")).toBeInTheDocument();
  });

  it("shows the next-stage button for an editor role and calls the transition endpoint", async () => {
    postMock.mockResolvedValue({});
    const onTransitioned = vi.fn();
    render(<ReviewStageBadge mappingId={5} reviewStage="draft" role="admin" onTransitioned={onTransitioned} />);

    const button = screen.getByRole("button", { name: "→ Pending Review" });
    fireEvent.click(button);
    await waitFor(() => expect(postMock).toHaveBeenCalledWith(
      "/api/v1/mappings/5/review/transition", { to_stage: "pending_review" },
    ));
    await waitFor(() => expect(onTransitioned).toHaveBeenCalled());
  });

  it("hides transition controls for a viewer role", () => {
    render(<ReviewStageBadge mappingId={1} reviewStage="draft" role="viewer" onTransitioned={vi.fn()} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("offers a revise-to-draft action at production_ready instead of a forward transition (B-E13-08)", () => {
    render(<ReviewStageBadge mappingId={1} reviewStage="production_ready" role="admin" onTransitioned={vi.fn()} />);
    expect(screen.getByRole("button", { name: "→ Draft" })).toBeInTheDocument();
  });

  it("offers Reject for an in-review stage but not for draft or production_ready", () => {
    const { rerender } = render(<ReviewStageBadge mappingId={1} reviewStage="pending_review" role="admin" onTransitioned={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();

    rerender(<ReviewStageBadge mappingId={1} reviewStage="draft" role="admin" onTransitioned={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();

    rerender(<ReviewStageBadge mappingId={1} reviewStage="production_ready" role="admin" onTransitioned={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("shows an error message when the transition request fails, without crashing", async () => {
    postMock.mockRejectedValue(new Error("role cannot make this transition"));
    render(<ReviewStageBadge mappingId={1} reviewStage="pending_review" role="analyst" onTransitioned={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "→ Business Approved" }));
    await waitFor(() => expect(screen.getByText("Transition failed.")).toBeInTheDocument());
  });

  it("offers the resubmit-to-draft transition for a rejected mapping", () => {
    render(<ReviewStageBadge mappingId={1} reviewStage="rejected" role="admin" onTransitioned={vi.fn()} />);
    expect(screen.getByRole("button", { name: "→ Draft" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("sends the prompted note as a steward comment when rejecting (E16-3)", async () => {
    postMock.mockResolvedValue({});
    vi.spyOn(window, "prompt").mockReturnValue("Needs another look at PII columns");
    render(<ReviewStageBadge mappingId={7} reviewStage="pending_review" role="admin" onTransitioned={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith(
      "/api/v1/mappings/7/review/transition",
      { to_stage: "rejected", note: "Needs another look at PII columns" },
    ));
  });

  it("does not transition when the reject prompt is cancelled", () => {
    vi.spyOn(window, "prompt").mockReturnValue(null);
    render(<ReviewStageBadge mappingId={7} reviewStage="pending_review" role="admin" onTransitioned={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(postMock).not.toHaveBeenCalled();
  });

  it("rejects with no note when the prompt is left blank", async () => {
    postMock.mockResolvedValue({});
    vi.spyOn(window, "prompt").mockReturnValue("   ");
    render(<ReviewStageBadge mappingId={7} reviewStage="pending_review" role="admin" onTransitioned={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith(
      "/api/v1/mappings/7/review/transition", { to_stage: "rejected", note: undefined },
    ));
  });
});
