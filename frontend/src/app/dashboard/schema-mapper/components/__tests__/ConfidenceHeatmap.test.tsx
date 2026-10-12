import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConfidenceHeatmap } from "../ConfidenceHeatmap";
import type { AISuggestion } from "../../lib/types";

function suggestion(source: string, target: string, confidence: number, id: number): AISuggestion {
  return {
    id,
    mapping_id: 1,
    source_table: source,
    source_column: `source_${id}`,
    source_type: "text",
    target_table: target,
    target_column: `target_${id}`,
    target_type: "text",
    confidence,
    reason: null,
    components: null,
    suggested_transformation: null,
    transformation_note: null,
    status: "pending",
    created_at: "2026-07-17T00:00:00Z",
    decided_at: null,
    decided_by: null,
  };
}

describe("ConfidenceHeatmap", () => {
  it("renders a source-target matrix using real average confidence", () => {
    render(<ConfidenceHeatmap suggestions={[
      suggestion("crm", "warehouse", 80, 1),
      suggestion("crm", "warehouse", 60, 2),
      suggestion("billing", "finance", 0.9, 3),
    ]} />);
    expect(screen.getByRole("table", { name: "Table confidence heatmap" })).toBeInTheDocument();
    expect(screen.getByText("70%")).toHaveAttribute("title", "2 matches, 70% average confidence");
    expect(screen.getByText("90%")).toHaveAttribute("title", "1 match, 90% average confidence");
    expect(screen.getAllByText("—")).toHaveLength(2);
  });

  it("does not render an empty matrix", () => {
    render(<ConfidenceHeatmap suggestions={[]} />);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
