import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EdgeInspector from "../EdgeInspector";
import type { FieldMapping } from "../../lib/types";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { get: getMock },
  ApiError: class ApiError extends Error {},
}));

function makeEdge(overrides: Partial<FieldMapping> = {}): FieldMapping {
  return {
    id: 1,
    mapping_id: 10,
    target: { table: "customers", column: "full_name", type: "TEXT", nullable: true, primary_key: false },
    sources: [{ table: "users", column: "name", type: "TEXT" }],
    transformation: { kind: "trim" },
    origin: "manual",
    ai_confidence: null,
    audit: {},
    created_at: "2026-07-19T00:00:00Z",
    updated_at: "2026-07-19T00:00:00Z",
    ...overrides,
  };
}

describe("EdgeInspector preview (Enterprise v2, E10-7)", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("shows nothing until Run preview is clicked", () => {
    render(<EdgeInspector edge={makeEdge()} mappingId={10} role="admin" canEdit onEdit={() => {}} onDelete={() => {}} />);
    expect(getMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "▶ Run preview" })).toBeInTheDocument();
  });

  it("fetches and renders preview rows on click", async () => {
    getMock.mockResolvedValue({
      available: true, reason: null,
      rows: [
        { source_values: { name: "  alice  " }, output: "alice" },
        { source_values: { name: "Bob" }, output: "Bob" },
      ],
    });
    render(<EdgeInspector edge={makeEdge()} mappingId={10} role="admin" canEdit onEdit={() => {}} onDelete={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: "▶ Run preview" }));
    expect(getMock).toHaveBeenCalledWith("/api/v1/mappings/10/edges/1/preview");
    await waitFor(() => expect(screen.getAllByText(/alice/).length).toBeGreaterThan(0));
    expect(screen.getByText(/name=\s*alice\s*/)).toBeInTheDocument();
    expect(screen.getByText(/→\s*Bob/)).toBeInTheDocument();
  });

  it("shows an honest unavailable reason instead of fabricating rows", async () => {
    getMock.mockResolvedValue({
      available: false,
      reason: "Lookup transformations require a live join and cannot be previewed on sample data.",
      rows: [],
    });
    render(<EdgeInspector edge={makeEdge({ transformation: { kind: "lookup", table: "t", key_column: "k", value_column: "v" } })} mappingId={10} role="admin" canEdit onEdit={() => {}} onDelete={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "▶ Run preview" }));
    await waitFor(() => expect(screen.getByText(/cannot be previewed/)).toBeInTheDocument());
  });

  it("shows a null marker for a null source or output value, not a blank/fake value", async () => {
    getMock.mockResolvedValue({
      available: true, reason: null,
      rows: [{ source_values: { email: null }, output: "unknown@example.com" }],
    });
    render(<EdgeInspector edge={makeEdge()} mappingId={10} role="admin" canEdit onEdit={() => {}} onDelete={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "▶ Run preview" }));
    await waitFor(() => expect(screen.getByText(/email=∅/)).toBeInTheDocument());
  });

  it("shows an error message when the preview request fails", async () => {
    getMock.mockRejectedValue(new Error("preview backend unreachable"));
    render(<EdgeInspector edge={makeEdge()} mappingId={10} role="admin" canEdit onEdit={() => {}} onDelete={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "▶ Run preview" }));
    await waitFor(() => expect(screen.getByText("preview backend unreachable")).toBeInTheDocument());
  });

  it("resets stale preview state when a different edge is selected", async () => {
    getMock.mockResolvedValue({ available: true, reason: null, rows: [{ source_values: { name: "alice" }, output: "alice" }] });
    const { rerender } = render(<EdgeInspector edge={makeEdge({ id: 1 })} mappingId={10} role="admin" canEdit onEdit={() => {}} onDelete={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "▶ Run preview" }));
    await waitFor(() => expect(screen.getAllByText(/alice/).length).toBeGreaterThan(0));

    rerender(<EdgeInspector edge={makeEdge({ id: 2 })} mappingId={10} role="admin" canEdit onEdit={() => {}} onDelete={() => {}} />);
    expect(screen.queryAllByText(/alice/).length).toBe(0);
  });

  it("disables the preview button when there is no mapping context", () => {
    render(<EdgeInspector edge={makeEdge()} mappingId={null} role="admin" canEdit onEdit={() => {}} onDelete={() => {}} />);
    expect(screen.getByRole("button", { name: "▶ Run preview" })).toBeDisabled();
  });
});
