import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CommentsPanel from "../CommentsPanel";

const { getMock, postMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(), postMock: vi.fn(), deleteMock: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  api: { get: getMock, post: postMock, delete: deleteMock },
  ApiError: class ApiError extends Error {},
}));

function makeAnnotation(overrides: Partial<{
  id: number; author: string; body: string; kind: "comment" | "steward_comment";
  review_stage: string | null;
}> = {}) {
  return {
    id: 1, mapping_id: 10, edge_id: null, parent_id: null,
    kind: "comment" as const, review_stage: null,
    author: "admin@test.local", body: "Looks good", created_at: "2026-07-21T00:00:00Z",
    ...overrides,
  };
}

describe("CommentsPanel (Enterprise v2, E16-1/2)", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    deleteMock.mockReset();
  });

  it("shows an honest empty state when there are no comments", async () => {
    getMock.mockResolvedValue([]);
    render(<CommentsPanel mappingId={10} role="admin" currentUserEmail="admin@test.local" />);
    expect(await screen.findByText("No comments yet.")).toBeInTheDocument();
  });

  it("renders comments with author and body", async () => {
    getMock.mockResolvedValue([makeAnnotation()]);
    render(<CommentsPanel mappingId={10} role="admin" currentUserEmail="admin@test.local" />);
    expect(await screen.findByText("Looks good")).toBeInTheDocument();
    expect(screen.getByText("admin@test.local")).toBeInTheDocument();
  });

  it("visually distinguishes a steward comment with its review stage", async () => {
    getMock.mockResolvedValue([makeAnnotation({ kind: "steward_comment", review_stage: "rejected", body: "Needs work" })]);
    render(<CommentsPanel mappingId={10} role="admin" currentUserEmail="admin@test.local" />);
    expect(await screen.findByText(/Steward · rejected/)).toBeInTheDocument();
  });

  it("posts a new comment and refetches the list", async () => {
    getMock.mockResolvedValueOnce([]).mockResolvedValueOnce([makeAnnotation({ body: "new comment" })]);
    postMock.mockResolvedValue({});
    render(<CommentsPanel mappingId={10} role="admin" currentUserEmail="admin@test.local" />);
    await screen.findByText("No comments yet.");

    fireEvent.change(screen.getByLabelText("New comment"), { target: { value: "new comment" } });
    fireEvent.click(screen.getByRole("button", { name: "Post comment" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith(
      "/api/v1/mappings/10/annotations", { body: "new comment" },
    ));
    expect(await screen.findByText("new comment")).toBeInTheDocument();
  });

  it("disables Post comment until a body is typed", async () => {
    getMock.mockResolvedValue([]);
    render(<CommentsPanel mappingId={10} role="admin" currentUserEmail="admin@test.local" />);
    await screen.findByText("No comments yet.");
    expect(screen.getByRole("button", { name: "Post comment" })).toBeDisabled();
  });

  it("shows the author their own delete button even as a non-admin", async () => {
    getMock.mockResolvedValue([makeAnnotation({ author: "analyst@test.local" })]);
    render(<CommentsPanel mappingId={10} role="analyst" currentUserEmail="analyst@test.local" />);
    expect(await screen.findByRole("button", { name: "Delete comment 1" })).toBeInTheDocument();
  });

  it("hides the delete button for someone else's comment when not an admin", async () => {
    getMock.mockResolvedValue([makeAnnotation({ author: "someone-else@test.local" })]);
    render(<CommentsPanel mappingId={10} role="analyst" currentUserEmail="analyst@test.local" />);
    await screen.findByText("Looks good");
    expect(screen.queryByRole("button", { name: "Delete comment 1" })).not.toBeInTheDocument();
  });

  it("deletes a comment and refetches", async () => {
    getMock.mockResolvedValueOnce([makeAnnotation()]).mockResolvedValueOnce([]);
    deleteMock.mockResolvedValue(undefined);
    render(<CommentsPanel mappingId={10} role="admin" currentUserEmail="admin@test.local" />);
    await screen.findByText("Looks good");

    fireEvent.click(screen.getByRole("button", { name: "Delete comment 1" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/api/v1/mappings/10/annotations/1"));
    await waitFor(() => expect(screen.getByText("No comments yet.")).toBeInTheDocument());
  });

  it("shows an error message when posting fails, without crashing", async () => {
    getMock.mockResolvedValue([]);
    postMock.mockRejectedValue(new Error("comments backend unreachable"));
    render(<CommentsPanel mappingId={10} role="admin" currentUserEmail="admin@test.local" />);
    await screen.findByText("No comments yet.");
    fireEvent.change(screen.getByLabelText("New comment"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Post comment" }));
    expect(await screen.findByText("comments backend unreachable")).toBeInTheDocument();
  });
});
