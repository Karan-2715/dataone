import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SqlWorkspaceView from "../../query-workspace/components/SqlWorkspaceView";
import type { QueryExecuteResult } from "../lib/types";

const { getMock, postMock, deleteMock, downloadPostMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  deleteMock: vi.fn(),
  downloadPostMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { get: getMock, post: postMock, delete: deleteMock, downloadPost: downloadPostMock },
  ApiError: class ApiError extends Error {
    constructor(public status: number, message: string) { super(message); }
  },
}));

function execResult(overrides: Partial<QueryExecuteResult> = {}): QueryExecuteResult {
  return {
    statement_type: "select",
    tables_referenced: ["widgets"],
    warnings: [],
    requires_confirmation: false,
    executed: true,
    columns: ["id", "name"],
    rows: [{ id: 1, name: "bolt" }],
    row_count: 1,
    affected_rows: null,
    page: 1,
    page_size: 100,
    has_more: false,
    truncated: false,
    duration_ms: 5,
    error: null,
    ...overrides,
  };
}

function routeGet(overrides: Record<string, unknown> = {}) {
  getMock.mockImplementation(async (path: string) => {
    if (path.startsWith("/api/v1/connectors/")) {
      return overrides.connectors ?? [{ id: 1, name: "widgets-db", type: "sqlite" }];
    }
    if (path.startsWith("/api/v1/catalog/")) {
      return overrides.catalog ?? { connection_id: 1, tables: [{ table_name: "widgets", columns: [{ column_name: "id" }] }] };
    }
    if (path.startsWith("/api/v1/query-studio/saved")) {
      return overrides.saved ?? [];
    }
    if (path.startsWith("/api/v1/query-studio/history")) {
      return overrides.history ?? { history: [], total: 0, page: 1, page_size: 50 };
    }
    throw new Error(`unexpected GET: ${path}`);
  });
}

const defaultConnections = [{ id: 1, name: "widgets-db", type: "sqlite" }];

describe("SqlWorkspaceView", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    deleteMock.mockReset();
    downloadPostMock.mockReset();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads connections and shows the run button", async () => {
    routeGet();
    render(<SqlWorkspaceView connections={defaultConnections} connectionId={1} setConnectionId={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/widgets-db/)).toBeInTheDocument());
    expect(screen.getByText(/Run \(/)).toBeInTheDocument();
  });

  it("runs a query from a loaded history entry and renders the results table", async () => {
    routeGet({
      history: {
        history: [{
          id: 1, actor: "a@x.com", sql: "SELECT * FROM widgets", connection_id: "1",
          statement_type: "select", outcome: "success", row_count: 1, duration_ms: 3,
          created_at: "2026-07-11T00:00:00Z",
        }],
        total: 1, page: 1, page_size: 50,
      },
    });
    postMock.mockResolvedValue(execResult());

    render(<SqlWorkspaceView connections={defaultConnections} connectionId={1} setConnectionId={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("SELECT * FROM widgets")).toBeInTheDocument());
    fireEvent.click(screen.getByText("SELECT * FROM widgets"));

    fireEvent.click(screen.getByText(/Run \(/));

    await waitFor(() => expect(screen.getByText("bolt")).toBeInTheDocument());
    const call = postMock.mock.calls.find((c) => c[0] === "/api/v1/query-studio/execute");
    expect(call?.[1]).toMatchObject({ connection_id: 1, sql: "SELECT * FROM widgets", confirm: false });
  });

  it("calls onPendingConfirmChange when a write statement requires confirmation", async () => {
    routeGet({
      saved: [{
        id: 5, connection_id: 1, name: "delete-all", sql_text: "DELETE FROM widgets",
        created_by: "a@x.com", created_at: "2026-07-11T00:00:00Z", updated_at: "2026-07-11T00:00:00Z",
      }],
    });
    const onPendingConfirmChange = vi.fn();
    postMock.mockResolvedValueOnce(execResult({
      statement_type: "delete", requires_confirmation: true, executed: false,
      warnings: ["This is a DELETE statement — pass confirm=true to execute it."],
      columns: [], rows: [], row_count: 0,
    }));
    postMock.mockResolvedValueOnce(execResult({
      statement_type: "delete", executed: true, affected_rows: 3, columns: [], rows: [], row_count: 0,
    }));

    render(
      <SqlWorkspaceView
        connections={defaultConnections}
        connectionId={1}
        setConnectionId={vi.fn()}
        onPendingConfirmChange={onPendingConfirmChange}
      />
    );
    fireEvent.click(screen.getByText("Saved"));
    await waitFor(() => expect(screen.getByText("delete-all")).toBeInTheDocument());
    fireEvent.click(screen.getByText("delete-all"));

    fireEvent.click(screen.getByText(/Run \(/));

    // The modal is rendered at shell level, not inside SqlWorkspaceView.
    // Verify the shell is notified of the pending confirm state.
    await waitFor(() => expect(onPendingConfirmChange).toHaveBeenCalledWith(true));

    // The second call with confirm=true should still go through
    // (the shell calls confirmWrite which triggers runQuery with confirm:true)
  });

  it("exports CSV via api.downloadPost with the current connection and SQL", async () => {
    routeGet({
      history: {
        history: [{
          id: 1, actor: "a@x.com", sql: "SELECT * FROM widgets", connection_id: "1",
          statement_type: "select", outcome: "success", row_count: 1, duration_ms: 3,
          created_at: "2026-07-11T00:00:00Z",
        }],
        total: 1, page: 1, page_size: 50,
      },
    });
    downloadPostMock.mockResolvedValue({ blob: new Blob(["a,b"]), filename: "query_studio_export.csv" });
    const createObjectURL = vi.fn(() => "blob:mock");
    (URL as unknown as { createObjectURL: typeof createObjectURL }).createObjectURL = createObjectURL;
    (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = vi.fn();

    render(<SqlWorkspaceView connections={defaultConnections} connectionId={1} setConnectionId={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("SELECT * FROM widgets")).toBeInTheDocument());
    fireEvent.click(screen.getByText("SELECT * FROM widgets"));

    fireEvent.click(screen.getByText("Export CSV"));

    await waitFor(() => expect(downloadPostMock).toHaveBeenCalledWith(
      "/api/v1/query-studio/export",
      expect.objectContaining({ connection_id: 1, sql: "SELECT * FROM widgets" }),
    ));
  });

  it("saves the current query and refreshes the saved list", async () => {
    routeGet({
      history: {
        history: [{
          id: 1, actor: "a@x.com", sql: "SELECT * FROM widgets", connection_id: "1",
          statement_type: "select", outcome: "success", row_count: 1, duration_ms: 3,
          created_at: "2026-07-11T00:00:00Z",
        }],
        total: 1, page: 1, page_size: 50,
      },
    });
    postMock.mockResolvedValue({ id: 9, connection_id: 1, name: "my query", sql_text: "SELECT * FROM widgets" });
    vi.spyOn(window, "prompt").mockReturnValue("my query");

    render(<SqlWorkspaceView connections={defaultConnections} connectionId={1} setConnectionId={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("SELECT * FROM widgets")).toBeInTheDocument());
    fireEvent.click(screen.getByText("SELECT * FROM widgets"));

    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith(
      "/api/v1/query-studio/saved",
      expect.objectContaining({ connection_id: 1, name: "my query", sql_text: "SELECT * FROM widgets" }),
    ));
  });

  it("applies externalSqlText when passed as a prop", async () => {
    routeGet({ connectors: [
      { id: 1, name: "widgets-db", type: "sqlite" },
      { id: 2, name: "other-db", type: "postgres" },
    ] });

    render(
      <SqlWorkspaceView
        connections={[
          { id: 1, name: "widgets-db", type: "sqlite" },
          { id: 2, name: "other-db", type: "postgres" },
        ]}
        connectionId={1}
        setConnectionId={vi.fn()}
        externalSqlText="SELECT * FROM customers"
      />
    );

    // CodeMirror splits the line into per-token spans for syntax
    // highlighting, so there's no single text node with the full string —
    // check the editor content container's combined text instead.
    await waitFor(() => {
      const editorContent = document.querySelector(".cm-content");
      expect(editorContent?.textContent).toContain("SELECT * FROM customers");
    });
  });
});