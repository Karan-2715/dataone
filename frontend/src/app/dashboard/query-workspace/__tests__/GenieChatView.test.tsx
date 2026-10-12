import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import GenieChatView, { type GenieResult } from "../components/GenieChatView";

const get = vi.fn();
const post = vi.fn();
vi.mock("@/lib/api", () => ({
  api: { get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) },
  ApiError: class ApiError extends Error {},
}));

const CONNECTIONS = [
  { id: 1, name: "widgets-db", type: "sqlite" },
  { id: 6, name: "Databricks — me@x.com", type: "databricks" },
  { id: 7, name: "Databricks Workspace (Auto)", type: "databricks" },
];

const SPACES = {
  spaces: [
    { space_id: "space-aaaa-1111", title: "DataOne AskData", description: "Run quality" },
    { space_id: "space-bbbb-2222", title: "Bakehouse", description: null },
    { space_id: "space-auto-3333", title: "DataOne data: workspace.default", description: null, auto: true },
  ],
  configured: true, default_space_id: "space-aaaa-1111",
};

const SCHEMA = {
  schema: {
    "workspace.default.titanic": [], "workspace.default.company_stocks": [], "workspace.dataone_dq.file_quality_results": [],
    "workspace.empty.(empty_schema)": [], "samples.tpch.orders": [],
  },
};

const result = (over: Partial<GenieResult> = {}): GenieResult => ({
  status: "COMPLETED", done: true, answer: "Project demo scored highest.", sql: "SELECT project, score FROM q",
  sql_title: null, sql_description: "Latest score per project", columns: [{ name: "project", type: "STRING" }, { name: "score", type: "DOUBLE" }],
  rows: [["demo", "98.1"], ["churn", "91"]], row_count: 2, truncated: false, suggested_questions: ["By month?"], error: null, ...over,
});

function setup(polls: Partial<GenieResult>[] = [{}]) {
  get.mockImplementation(async (url: string) => {
    if (url.includes("/genie/spaces")) return SPACES;
    if (url.includes("/connectors/") && url.endsWith("/schema")) return SCHEMA;
    const next = polls.length > 1 ? polls.shift()! : polls[0];
    return result(next);
  });
  post.mockImplementation(async (url: string) => {
    if (url.endsWith("/genie/data-space")) return { space_id: "space-scoped-9999", title: "DataOne data: x", created: true, tables: ["titanic"] };
    return { space_id: "space-aaaa-1111", conversation_id: "c1", message_id: "m1" };
  });
}

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });

async function renderView(props: Partial<React.ComponentProps<typeof GenieChatView>> = {}) {
  const utils = render(<GenieChatView connections={CONNECTIONS} connectionId={6} onEditInSql={vi.fn()} {...props} />);
  await flush();
  return utils;
}

async function ask(text: string) {
  fireEvent.change(screen.getByLabelText(/ask genie a question/i), { target: { value: text } });
  fireEvent.click(screen.getByTitle("Ask"));
  await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
}

const select = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const options = (label: string) => Array.from((screen.getByLabelText(label) as HTMLSelectElement).options).map((o) => o.textContent);

describe("GenieChatView", () => {
  beforeEach(() => { vi.useFakeTimers(); get.mockReset(); post.mockReset(); window.localStorage.clear(); });
  afterEach(() => vi.useRealTimers());

  describe("connection, agent and data pickers", () => {
    it("lists only Databricks connections and curated agents", async () => {
      setup();
      await renderView();
      expect(options("Connection")).toEqual(["Databricks — me@x.com", "Databricks Workspace (Auto)"]);
      expect(options("Genie agent")).toEqual(["DataOne AskData", "Bakehouse"]); // auto-created per-selection agents are hidden
      expect(screen.getByLabelText("Genie agent")).toHaveValue("space-aaaa-1111");
      expect(screen.queryByText(/askdata \(connection\)/i)).not.toBeInTheDocument();
    });

    it("starts on the shared connection when it is Databricks, else the first Databricks one", async () => {
      setup();
      const first = await renderView({ connectionId: 7 });
      expect(screen.getByLabelText("Connection")).toHaveValue("7");
      first.unmount();
      await renderView({ connectionId: 1 }); // a sqlite connection is shared: fall back to the first Databricks one
      expect(screen.getByLabelText("Connection")).toHaveValue("6");
    });

    it("picking another connection reloads its catalogs and is used for Edit in SQL", async () => {
      setup();
      const onEdit = vi.fn();
      await renderView({ onEditInSql: onEdit });
      select("Connection", "7");
      await flush();
      expect(get).toHaveBeenCalledWith("/api/v1/connectors/7/schema");
      await ask("top projects?");
      fireEvent.click(screen.getByRole("button", { name: /edit in sql/i }));
      expect(onEdit).toHaveBeenCalledWith(7, "SELECT project, score FROM q");
    });

    it("loads catalogs, schemas and tables from the selected connection", async () => {
      setup();
      await renderView();
      expect(get).toHaveBeenCalledWith("/api/v1/connectors/6/schema");
      expect(options("Catalog")).toEqual(["Catalog…", "samples", "workspace"]);
      select("Catalog", "workspace");
      expect(options("Schema")).toEqual(["Schema…", "dataone_dq", "default", "empty"]);
      select("Schema", "default");
      expect(options("Table")).toEqual(["All tables in schema…", "company_stocks", "titanic"]);
    });
  });

  describe("asking", () => {
    it("asks the selected agent, shows progress, then the answer, generated SQL and rows", async () => {
      setup([{ status: "EXECUTING_QUERY", done: false, answer: null, sql: null, rows: [], columns: [] }, {}]);
      const onDone = vi.fn();
      await renderView({ onBackgroundComplete: onDone });
      await ask("top projects?");
      expect(post).toHaveBeenCalledWith("/api/v1/genie/chat", { question: "top projects?", space_id: "space-aaaa-1111", conversation_id: null });
      expect(post).not.toHaveBeenCalledWith("/api/v1/genie/data-space", expect.anything());
      expect(screen.getByRole("status")).toHaveTextContent(/running the query/i);
      await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
      expect(screen.getByText("Project demo scored highest.")).toBeInTheDocument();
      expect(screen.getByText("98.1")).toBeInTheDocument();
      expect(screen.getByText(/showing 2 of 2 rows/i)).toBeInTheDocument();
      expect(onDone).toHaveBeenCalled();
      fireEvent.click(screen.getByText(/view generated sql/i));
      expect(screen.getByText("SELECT project, score FROM q")).toBeInTheDocument();
    });

    it("follow-ups reuse the conversation; chips send as follow-ups", async () => {
      setup();
      await renderView();
      await ask("top projects?");
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      fireEvent.click(screen.getByRole("button", { name: "By month?" }));
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      expect(post).toHaveBeenLastCalledWith("/api/v1/genie/chat", { question: "By month?", space_id: "space-aaaa-1111", conversation_id: "c1" });
    });

    it("Edit in SQL hands the generated SQL and the Databricks connection to the SQL editor", async () => {
      setup();
      const onEdit = vi.fn();
      await renderView({ onEditInSql: onEdit });
      await ask("top projects?");
      fireEvent.click(screen.getByRole("button", { name: /edit in sql/i }));
      expect(onEdit).toHaveBeenCalledWith(6, "SELECT project, score FROM q");
    });

    it("shows a Genie failure instead of an empty answer", async () => {
      setup([{ status: "FAILED", answer: null, sql: null, rows: [], columns: [], suggested_questions: [], error: "[INSUFFICIENT_PERMISSIONS] no SELECT" }]);
      await renderView();
      await ask("anything");
      expect(screen.getByText("[INSUFFICIENT_PERMISSIONS] no SELECT")).toBeInTheDocument();
    });

    it("reports when the request itself fails", async () => {
      setup();
      post.mockRejectedValue(new Error("Genie request failed: workspace unreachable"));
      await renderView();
      await ask("anything");
      expect(screen.getByText(/couldn't reach genie: .*workspace unreachable/i)).toBeInTheDocument();
    });

    it("pre-fills (does not send) a handed-off question", async () => {
      setup();
      await renderView({ prefillQuestion: "show failing tables" });
      expect(screen.getByLabelText(/ask genie a question/i)).toHaveValue("show failing tables");
      expect(post).not.toHaveBeenCalled();
    });
  });

  describe("scoping a question to a catalog / schema / table", () => {
    async function pickTitanic() {
      select("Catalog", "workspace");
      select("Schema", "default");
      select("Table", "titanic");
    }

    it("creates/reuses a Genie agent over the selected table and asks it", async () => {
      setup();
      await renderView();
      await pickTitanic();
      expect(screen.getByLabelText("Genie agent")).toBeDisabled();
      expect(screen.getByText("Auto: workspace.default.titanic")).toBeInTheDocument();
      await ask("how many columns?");
      expect(post).toHaveBeenCalledWith("/api/v1/genie/data-space", { catalog: "workspace", schema: "default", table: "titanic" });
      expect(post).toHaveBeenCalledWith("/api/v1/genie/chat", { question: "how many columns?", space_id: "space-scoped-9999", conversation_id: null });
    });

    it("a schema without a table scopes the agent to every table in it", async () => {
      setup();
      await renderView();
      select("Catalog", "workspace");
      select("Schema", "default");
      await ask("what tables are here?");
      expect(post).toHaveBeenCalledWith("/api/v1/genie/data-space", { catalog: "workspace", schema: "default", table: null });
    });

    it("prepares the agent once per selection", async () => {
      setup();
      await renderView();
      await pickTitanic();
      await ask("first");
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      await ask("second");
      expect(post.mock.calls.filter((c) => c[0] === "/api/v1/genie/data-space")).toHaveLength(1);
    });

    it("a catalog alone is not enough: asks for a schema and blocks sending", async () => {
      setup();
      await renderView();
      select("Catalog", "workspace");
      expect(screen.getByRole("status")).toHaveTextContent(/pick a schema/i);
      fireEvent.change(screen.getByLabelText(/ask genie a question/i), { target: { value: "hi" } });
      expect(screen.getByTitle("Ask")).toBeDisabled();
    });

    it("changing the selection starts a fresh conversation; Clear selection returns to the agent", async () => {
      setup();
      await renderView();
      await pickTitanic();
      await ask("first");
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      expect(screen.getByText("first")).toBeInTheDocument();
      select("Table", "company_stocks");
      expect(screen.queryByText("first")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /clear selection/i }));
      expect(screen.getByLabelText("Genie agent")).toBeEnabled();
      expect(screen.getByLabelText("Catalog")).toHaveValue("");
    });

    it("shows why a scoped agent could not be prepared", async () => {
      setup();
      post.mockImplementation(async (url: string) => {
        if (url.endsWith("/genie/data-space")) throw new Error("The app cannot read workspace.default.titanic: PERMISSION_DENIED");
        return {};
      });
      await renderView();
      await pickTitanic();
      await ask("how many columns?");
      expect(screen.getByText(/cannot read workspace\.default\.titanic/i)).toBeInTheDocument();
    });
  });

  it("switching agent starts a fresh conversation and remembers the choice", async () => {
    setup();
    await renderView();
    select("Genie agent", "space-bbbb-2222");
    expect(window.localStorage.getItem("dataone.genie.space")).toBe("space-bbbb-2222");
    expect(screen.getByRole("button", { name: /what can i ask about this data/i })).toBeInTheDocument(); // generic starters
  });

  it("explains when no Genie agent is available but still allows picking data", async () => {
    get.mockImplementation(async (url: string) => {
      if (url.includes("/genie/spaces")) throw new Error("No Genie agent is configured.");
      return SCHEMA;
    });
    await renderView();
    expect(screen.getByRole("alert")).toHaveTextContent(/no genie agent is configured/i);
    select("Catalog", "workspace");
    select("Schema", "default");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(); // scoped: the app will create an agent for this data
  });
});
