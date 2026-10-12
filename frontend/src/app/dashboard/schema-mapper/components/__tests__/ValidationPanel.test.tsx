import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ValidationPanel from "../ValidationPanel";
import type { AISuggestion, FieldMapping, ValidationResponse } from "../../lib/types";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

function edge(overrides: Partial<FieldMapping> = {}): FieldMapping {
  return {
    id: 5,
    mapping_id: 1,
    target: { table: "customers", column: "email_address" },
    sources: [{ table: "raw_customers", column: "email" }],
    transformation: { kind: "direct" },
    origin: "manual",
    ai_confidence: null,
    audit: {},
    created_at: "2026-07-14T00:00:00Z",
    updated_at: "2026-07-14T00:00:00Z",
    ...overrides,
  };
}

function suggestion(overrides: Partial<AISuggestion> = {}): AISuggestion {
  return {
    id: 9,
    mapping_id: 1,
    target_table: "customers",
    target_column: "phone",
    target_type: "varchar",
    source_table: "raw_customers",
    source_column: "phone_number",
    source_type: "varchar",
    confidence: 40,
    reason: null,
    components: null,
    suggested_transformation: null,
    transformation_note: null,
    status: "pending",
    created_at: "2026-07-14T00:00:00Z",
    decided_at: null,
    decided_by: null,
    ...overrides,
  };
}

function validationWith(issues: ValidationResponse["issues"]): ValidationResponse {
  return { mapping_id: 1, ok_count: 0, warning_count: 0, blocking_count: issues.length, issues };
}

describe("ValidationPanel", () => {
  beforeEach(() => {
    sessionStorage.clear();
    pushMock.mockClear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("calls onClose when the close button is clicked (uiux bug report: dead close button)", () => {
    const onClose = vi.fn();
    render(
      <ValidationPanel
        validation={validationWith([
          { edge_id: 5, suggestion_id: null, verdict: "blocking", message: "type mismatch" },
        ])}
        onClose={onClose}
        onJumpToEdge={vi.fn()}
      />
    );
    fireEvent.click(screen.getByLabelText("Close validation panel"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("resolves an edge-linked issue's source table/column via the edges array", () => {
    const validation = validationWith([
      { edge_id: 5, suggestion_id: null, verdict: "blocking", message: "type mismatch" },
    ]);
    render(
      <ValidationPanel
        validation={validation}
        onClose={vi.fn()}
        onJumpToEdge={vi.fn()}
        edges={[edge()]}
        suggestions={[]}
        sourceConnectionId={10}
      />
    );

    fireEvent.click(screen.getByLabelText("Investigate edge"));

    const raw = sessionStorage.getItem("query-workspace-handoff");
    expect(raw).not.toBeNull();
    const payload = JSON.parse(raw as string);
    expect(payload.connectionId).toBe(10);
    expect(payload.sql).toContain("raw_customers");
    expect(payload.sql).toContain("email");
    expect(pushMock).toHaveBeenCalledWith("/dashboard/query-workspace");
  });

  it("resolves a suggestion-linked issue's source table/column via the suggestions array, not a placeholder", () => {
    const validation = validationWith([
      { edge_id: null, suggestion_id: 9, verdict: "lossy_warning", message: "low confidence" },
    ]);
    render(
      <ValidationPanel
        validation={validation}
        onClose={vi.fn()}
        onJumpToEdge={vi.fn()}
        edges={[]}
        suggestions={[suggestion()]}
        sourceConnectionId={10}
      />
    );

    fireEvent.click(screen.getByLabelText("Investigate suggestion issue"));

    const raw = sessionStorage.getItem("query-workspace-handoff");
    expect(raw).not.toBeNull();
    const payload = JSON.parse(raw as string);
    expect(payload.sql).not.toContain("related_table");
    expect(payload.sql).toContain("raw_customers");
    expect(payload.sql).toContain("phone_number");
  });

  it("does not render an Investigate action for a suggestion issue when the suggestion can't be resolved", () => {
    const validation = validationWith([
      { edge_id: null, suggestion_id: 999, verdict: "lossy_warning", message: "low confidence" },
    ]);
    render(
      <ValidationPanel
        validation={validation}
        onClose={vi.fn()}
        onJumpToEdge={vi.fn()}
        edges={[]}
        suggestions={[suggestion()]}
        sourceConnectionId={10}
      />
    );
    expect(screen.queryByLabelText("Investigate suggestion issue")).toBeNull();
  });
});
