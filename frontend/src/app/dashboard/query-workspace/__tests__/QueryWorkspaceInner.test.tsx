import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import QueryWorkspaceInner from "../QueryWorkspaceInner";
import { writeWorkspaceHandoff } from "../lib/handoff";
import type { QueryExecuteResult } from "../../query-studio/lib/types";

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: getMock,
    post: postMock,
    delete: vi.fn(),
    downloadPost: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    constructor(public status: number, message: string) { super(message); }
  },
}));

let currentSearchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useSearchParams: () => currentSearchParams,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

Element.prototype.scrollIntoView = vi.fn();

const CONNECTIONS = [
  { id: 1, name: "widgets-db", type: "sqlite" },
  { id: 2, name: "Databricks — me@x.com", type: "databricks" },
];
const GENIE_ANSWER = {
  space_id: "space-aaaa-1111", conversation_id: "c1", message_id: "m1", status: "COMPLETED", done: true,
  answer: "There are 3 widgets.", sql: "SELECT COUNT(*) FROM widgets", sql_title: null, sql_description: null,
  columns: [{ name: "n", type: "INT" }], rows: [["3"]], row_count: 1, truncated: false, suggested_questions: [], error: null,
};

function execResult(overrides: Partial<QueryExecuteResult> = {}): QueryExecuteResult {
  return {
    statement_type: "select",
    tables_referenced: ["widgets"],
    warnings: [],
    requires_confirmation: false,
    executed: true,
    columns: ["id"],
    rows: [{ id: 1 }],
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
    if (path.startsWith("/api/v1/genie/spaces")) return { spaces: [{ space_id: "space-aaaa-1111", title: "DataOne AskData", description: null }], configured: true, default_space_id: "space-aaaa-1111" };
    if (path.startsWith("/api/v1/genie/chat/")) return GENIE_ANSWER;
    if (path.startsWith("/api/v1/connectors/") && path.endsWith("/schema")) return { schema: { "workspace.default.widgets": [] } };
    if (path.startsWith("/api/v1/connectors/")) return CONNECTIONS;
    if (path.startsWith("/api/v1/catalog/")) return { connection_id: 1, tables: [] };
    if (path.startsWith("/api/v1/query-studio/saved")) return overrides.saved ?? [];
    if (path.startsWith("/api/v1/query-studio/history")) return overrides.history ?? { history: [], total: 0, page: 1, page_size: 50 };
    throw new Error(`unexpected GET: ${path}`);
  });
}

describe("QueryWorkspaceInner", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    sessionStorage.clear();
    currentSearchParams = new URLSearchParams();
    routeGet();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("defaults to the Genie (Databricks) chat with the SQL editor mounted but hidden; no AskData engine or Ask Genie toggle", async () => {
    render(<QueryWorkspaceInner />);
    await waitFor(() => expect(screen.getByLabelText("Catalog")).toBeInTheDocument());
    expect(screen.getByLabelText("Ask Genie a question about your data")).toBeInTheDocument();
    // connection + agent + catalog/schema/table pickers live inside the Genie chat
    await waitFor(() => expect(screen.getAllByLabelText("Connection")[0]).toHaveValue("2")); // the chat's own picker (the SQL editor has one too)
    expect(screen.getByLabelText("Genie agent")).toBeInTheDocument();
    expect(screen.getByLabelText("Schema")).toBeInTheDocument();
    expect(screen.getByLabelText("Table")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Genie (Databricks)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText(/AskData \(connection\)/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ask Genie" })).not.toBeInTheDocument();

    // SQL mode's editor should already be in the DOM, just hidden.
    expect(screen.getByText("SQL Workspace")).toBeInTheDocument();
  });

  it("initializes mode from the ?mode= search param", async () => {
    currentSearchParams = new URLSearchParams("mode=sql");
    render(<QueryWorkspaceInner />);
    await waitFor(() => expect(screen.getByText(/Run \(/)).toBeInTheDocument());
  });

  it("preserves a SQL draft across a mode switch instead of remounting", async () => {
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
    render(<QueryWorkspaceInner />);

    fireEvent.click(screen.getByRole("button", { name: "SQL Editor" }));
    await waitFor(() => expect(screen.getByText("SELECT * FROM widgets")).toBeInTheDocument());
    fireEvent.click(screen.getByText("SELECT * FROM widgets"));
    await waitFor(() => {
      expect(document.querySelector(".cm-content")?.textContent).toContain("SELECT * FROM widgets");
    });

    // Switch away and back — SqlWorkspaceView was never unmounted, so the
    // loaded draft must still be there (not reset to empty).
    fireEvent.click(screen.getByRole("button", { name: "Genie (Databricks)" }));
    fireEvent.click(screen.getByRole("button", { name: "SQL Editor" }));

    expect(document.querySelector(".cm-content")?.textContent).toContain("SELECT * FROM widgets");
  });

  it("hands Genie's generated SQL to the SQL editor and switches to it without navigation", async () => {
    postMock.mockResolvedValue({ space_id: "space-aaaa-1111", conversation_id: "c1", message_id: "m1" });
    render(<QueryWorkspaceInner />);
    await waitFor(() => expect(screen.getByLabelText("Genie agent")).toHaveValue("space-aaaa-1111"));

    fireEvent.change(screen.getByLabelText("Ask Genie a question about your data"), { target: { value: "how many widgets?" } });
    fireEvent.click(screen.getByTitle("Ask"));
    await waitFor(() => expect(screen.getByText("There are 3 widgets.")).toBeInTheDocument(), { timeout: 6000 });

    fireEvent.click(screen.getByRole("button", { name: /edit in sql/i }));

    await waitFor(() => {
      const editorContent = document.querySelector(".cm-content");
      expect(editorContent?.textContent).toContain("SELECT COUNT(*) FROM widgets");
    });
    expect(screen.getByRole("button", { name: "SQL Editor" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("SQL Workspace")).toBeInTheDocument();
  });

  it("shows a retryable connection error instead of silently disabling the workspace", async () => {
    getMock.mockReset();
    let connectorsFailed = false;
    getMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/v1/genie/spaces")) return { spaces: [], configured: false, default_space_id: null };
      if (path.startsWith("/api/v1/connectors/") && path.endsWith("/schema")) return { schema: {} };
      if (path.startsWith("/api/v1/connectors/")) {
        if (!connectorsFailed) {
          connectorsFailed = true;
          throw new Error("connectors unavailable");
        }
        return CONNECTIONS;
      }
      if (path.startsWith("/api/v1/catalog/")) return { connection_id: 1, tables: [] };
      if (path.startsWith("/api/v1/query-studio/saved")) return [];
      if (path.startsWith("/api/v1/query-studio/history")) return { history: [], total: 0, page: 1, page_size: 50 };
      throw new Error(`unexpected GET: ${path}`);
    });

    render(<QueryWorkspaceInner />);

    await waitFor(() => expect(screen.getByText("connectors unavailable")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() => expect(screen.queryByText("connectors unavailable")).not.toBeInTheDocument());
    expect(screen.getAllByText(/widgets-db/).length).toBeGreaterThan(0);
  });

  it("applies a WorkspaceHandoff on mount, shows the banner, and its mode wins over ?mode=", async () => {
    currentSearchParams = new URLSearchParams("mode=ask");
    writeWorkspaceHandoff({
      connectionId: 1,
      mode: "sql",
      sql: "SELECT * FROM customers LIMIT 100;",
      banner: { sourceModule: "schema_intel", summary: "Drift on customers" },
    });

    render(<QueryWorkspaceInner />);

    await waitFor(() => expect(screen.getByText(/Drift on customers/)).toBeInTheDocument());
    expect(screen.getByText(/Schema Intel/)).toBeInTheDocument();
    await waitFor(() => {
      const editorContent = document.querySelector(".cm-content");
      expect(editorContent?.textContent).toContain("SELECT * FROM customers LIMIT 100;");
    });

    // The handoff key is cleared after being read once.
    expect(sessionStorage.getItem("query-workspace-handoff")).toBeNull();
  });

  it("keeps the write-confirm modal visible even after switching away from SQL mode", async () => {
    routeGet({
      saved: [{
        id: 5, connection_id: 1, name: "delete-all", sql_text: "DELETE FROM widgets",
        created_by: "a@x.com", created_at: "2026-07-11T00:00:00Z", updated_at: "2026-07-11T00:00:00Z",
      }],
    });
    postMock.mockResolvedValueOnce(execResult({
      statement_type: "delete", requires_confirmation: true, executed: false,
      warnings: ["This is a DELETE statement — pass confirm=true to execute it."],
      columns: [], rows: [], row_count: 0,
    }));

    render(<QueryWorkspaceInner />);
    fireEvent.click(screen.getByRole("button", { name: "SQL Editor" }));

    fireEvent.click(screen.getByText("Saved"));
    await waitFor(() => expect(screen.getByText("delete-all")).toBeInTheDocument());
    fireEvent.click(screen.getByText("delete-all"));

    fireEvent.click(screen.getByText(/Run \(/));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());

    // Switch to the Genie chat — the modal must still be visible (rendered at shell level).
    fireEvent.click(screen.getByRole("button", { name: "Genie (Databricks)" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
