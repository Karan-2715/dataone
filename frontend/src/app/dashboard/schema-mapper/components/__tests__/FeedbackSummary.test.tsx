import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import FeedbackSummary from "../FeedbackSummary";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { get: getMock } }));

describe("FeedbackSummary (Enterprise v2, E14-5)", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("renders nothing while loading", () => {
    getMock.mockReturnValue(new Promise(() => {}));
    const { container } = render(<FeedbackSummary />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing on a fetch error, never a crash", async () => {
    getMock.mockRejectedValue(new Error("unreachable"));
    const { container } = render(<FeedbackSummary />);
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("shows an honest empty message when there is no decided feedback yet", async () => {
    getMock.mockResolvedValue({
      total_decided: 0, total_accepted: 0, total_rejected: 0,
      acceptance_rate: 0.0, most_corrected: [],
    });
    render(<FeedbackSummary />);
    expect(await screen.findByText(/No AI-suggestion feedback recorded yet/)).toBeInTheDocument();
  });

  it("shows the acceptance rate and most-corrected pairs when data exists", async () => {
    getMock.mockResolvedValue({
      total_decided: 10, total_accepted: 7, total_rejected: 3,
      acceptance_rate: 70.0,
      most_corrected: [
        { source_column: "cust_name", target_column: "customer_full_name", accepted_count: 0, rejected_count: 3 },
      ],
    });
    render(<FeedbackSummary />);
    expect(await screen.findByText("AI feedback: 70% acceptance rate (10 decided)")).toBeInTheDocument();
    expect(screen.getByText(/cust_name → customer_full_name: 3 rejected/)).toBeInTheDocument();
  });
});
