import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SuggestionPanel from "../SuggestionPanel";
import type { AISuggestion } from "../../lib/types";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

function suggestion(overrides: Partial<AISuggestion> = {}): AISuggestion {
  return {
    id: 1,
    mapping_id: 1,
    target_table: "customers",
    target_column: "email_address",
    target_type: "varchar",
    source_table: "raw_customers",
    source_column: "email",
    source_type: "varchar",
    confidence: 62,
    reason: "name similarity",
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

describe("SuggestionPanel", () => {
  beforeEach(() => {
    sessionStorage.clear();
    pushMock.mockClear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("discloses the match reason and confidence contributors", () => {
    render(
      <SuggestionPanel
        pending={[suggestion({
          reason: "Similar names and compatible types",
          components: { name_similarity: 88, type_compatibility: 1, value_pattern: 0, semantic: 0.72 },
        })]}
        decided={[]}
        loading={false}
        role="analyst"
        onRequest={vi.fn()}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />
    );
    fireEvent.click(screen.getByText("Why this match?"));
    expect(screen.getByText("Similar names and compatible types")).toBeInTheDocument();
    expect(screen.getByText("Name similarity")).toBeInTheDocument();
    expect(screen.getByText("Type compatibility")).toBeInTheDocument();
    expect(screen.getByText("Semantic meaning")).toBeInTheDocument();
    expect(screen.queryByText("Value pattern")).not.toBeInTheDocument();
    expect(screen.getAllByRole("meter")).toHaveLength(3);
  });

  it("shows and applies the validated suggested transformation on accept", () => {
    const onAccept = vi.fn();
    render(
      <SuggestionPanel
        pending={[suggestion({
          suggested_transformation: { kind: "cast", from: "TEXT", to: "INTEGER" },
          transformation_note: "Source and target use different type families",
        })]}
        decided={[]}
        loading={false}
        role="admin"
        onRequest={vi.fn()}
        onAccept={onAccept}
        onReject={vi.fn()}
      />
    );
    expect(screen.getByText("Cast to INTEGER")).toBeInTheDocument();
    expect(screen.getByText("Source and target use different type families")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Accept suggestion"));
    expect(onAccept).toHaveBeenCalledWith(1, { kind: "cast", from: "TEXT", to: "INTEGER" });
  });

  it("renders a readable label for a suggested threshold-conditional (case) transformation", () => {
    render(
      <SuggestionPanel
        pending={[suggestion({
          suggested_transformation: {
            kind: "case", operator: ">", compare_value: 500_000_000,
            then_value: "Enterprise", else_value: "SMB",
          },
        })]}
        decided={[]}
        loading={false}
        role="admin"
        onRequest={vi.fn()}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />
    );
    expect(screen.getByText("If > 500000000 then Enterprise else SMB")).toBeInTheDocument();
  });

  it("writes a WorkspaceHandoff using the suggestion's own resolved source table/column", () => {
    render(
      <SuggestionPanel
        pending={[suggestion()]}
        decided={[]}
        loading={false}
        role="admin"
        onRequest={vi.fn()}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        sourceConnectionId={10}
      />
    );

    fireEvent.click(screen.getByLabelText("Investigate suggestion"));

    const raw = sessionStorage.getItem("query-workspace-handoff");
    expect(raw).not.toBeNull();
    const payload = JSON.parse(raw as string);
    expect(payload.connectionId).toBe(10);
    expect(payload.mode).toBe("sql");
    expect(payload.sql).toContain("raw_customers");
    expect(payload.sql).toContain("email");
    expect(payload.banner.summary).toContain("raw_customers.email");
    expect(pushMock).toHaveBeenCalledWith("/dashboard/query-workspace");
  });

  it("disables the AI Suggest button while a generation request is in flight (uiux bug report: not idempotent)", () => {
    const onRequest = vi.fn();
    render(
      <SuggestionPanel
        pending={[]}
        decided={[]}
        loading
        role="admin"
        onRequest={onRequest}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />
    );
    const button = screen.getByLabelText("Request AI suggestions");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Generating…");
    fireEvent.click(button);
    expect(onRequest).not.toHaveBeenCalled();
  });

  it("enables the AI Suggest button once generation is not in flight", () => {
    render(
      <SuggestionPanel
        pending={[]}
        decided={[]}
        loading={false}
        role="admin"
        onRequest={vi.fn()}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />
    );
    expect(screen.getByLabelText("Request AI suggestions")).not.toBeDisabled();
  });

  it("no-ops when the mapping has no source connection yet", () => {
    render(
      <SuggestionPanel
        pending={[suggestion()]}
        decided={[]}
        loading={false}
        role="admin"
        onRequest={vi.fn()}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        sourceConnectionId={null}
      />
    );
    fireEvent.click(screen.getByLabelText("Investigate suggestion"));
    expect(sessionStorage.getItem("query-workspace-handoff")).toBeNull();
    expect(pushMock).not.toHaveBeenCalled();
  });
});
