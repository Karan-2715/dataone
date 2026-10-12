import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SchemaMapperPage from "../page";
import type { AISuggestion, Mapping, Paginated } from "../lib/types";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));

vi.mock("@/lib/api", () => ({
  api: {
    get: getMock,
    post: postMock,
    put: vi.fn(),
    delete: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  },
  addUnauthorizedHandler: () => () => {},
}));

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

function draftMapping(overrides: Partial<Mapping> = {}): Mapping {
  return {
    id: 1,
    name: "CRM to DW",
    source_id: 10,
    target_id: 20,
    status: "draft",
    review_stage: "draft",
    current_version_id: null,
    created_by: "admin@test.local",
    created_at: "2026-07-22T00:00:00Z",
    updated_at: "2026-07-22T00:00:00Z",
    edges: [],
    ...overrides,
  };
}

function paginated<T>(items: T[]): Paginated<T> {
  return { items, total: items.length, limit: 50, offset: 0, has_more: false };
}

function mockEndpoints(mapping: Mapping, suggestions: AISuggestion[] = []) {
  getMock.mockImplementation(async (path: string) => {
    if (path === "/api/v1/auth/me") return { role: "admin", email: "admin@test.local" };
    if (path.startsWith("/api/v1/mappings/?")) return paginated([mapping]);
    if (path === `/api/v1/mappings/${mapping.id}`) return mapping;
    if (path.startsWith(`/api/v1/mappings/${mapping.id}/suggestions`)) return paginated(suggestions);
    if (path === `/api/v1/connectors/${mapping.source_id}/schema`) return { schema: {} };
    if (path === `/api/v1/connectors/${mapping.target_id}/schema`) return { schema: {} };
    if (path === "/api/v1/workspace-layout/schema-mapper") {
      return { workspace_key: "schema-mapper", layout: {} };
    }
    if (path === `/api/v1/mappings/${mapping.id}/annotations`) return [];
    if (path === `/api/v1/mappings/${mapping.id}/versions`) return { items: [] };
    throw new Error(`unexpected GET ${path}`);
  });
}

async function selectMapping(name: string) {
  const row = await screen.findByRole("button", { name: new RegExp(name) });
  fireEvent.click(row);
}

// jsdom has no compiled Tailwind stylesheet loaded, so Tailwind's `hidden`
// utility class never becomes a real `display: none` here — jest-dom's
// toBeVisible() can't observe it. Assert on the wrapper's class list
// directly instead (mirrors how QueryWorkspaceInner.test.tsx verifies its
// own identical hidden-class toggle: presence in the DOM, not computed
// visibility).
function modeWrapperOf(el: HTMLElement): HTMLElement {
  return el.parentElement as HTMLElement;
}

describe("SchemaMapperPage — Manual / AI Suggested mode toggle (mirrors Query Workspace's Ask/SQL)", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    pushMock.mockClear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows the toggle for a draft mapping, defaulting to Manual with the canvas visible", async () => {
    mockEndpoints(draftMapping());
    render(<SchemaMapperPage />);
    await selectMapping("CRM to DW");

    const group = await screen.findByRole("group", { name: "Schema mapper mode" });
    expect(group).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Manual" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "AI Suggested" })).toHaveAttribute("aria-pressed", "false");

    const canvas = await screen.findByLabelText("Schema mapping canvas");
    const suggestions = screen.getByLabelText("AI suggestions");
    expect(modeWrapperOf(canvas)).not.toHaveClass("hidden");
    expect(modeWrapperOf(suggestions)).toHaveClass("hidden");
  });

  it("switching to AI Suggested hides the canvas and shows suggestions, without unmounting either", async () => {
    mockEndpoints(draftMapping());
    render(<SchemaMapperPage />);
    await selectMapping("CRM to DW");
    const canvas = await screen.findByLabelText("Schema mapping canvas");
    const suggestions = screen.getByLabelText("AI suggestions");

    fireEvent.click(screen.getByRole("button", { name: "AI Suggested" }));

    expect(modeWrapperOf(suggestions)).not.toHaveClass("hidden");
    expect(modeWrapperOf(canvas)).toHaveClass("hidden");
    // Both are still in the DOM — neither was unmounted by the switch.
    expect(canvas).toBeInTheDocument();
    expect(suggestions).toBeInTheDocument();
  });

  it("switching modes does not re-fetch the canvas schema", async () => {
    mockEndpoints(draftMapping());
    render(<SchemaMapperPage />);
    await selectMapping("CRM to DW");
    await screen.findByLabelText("Schema mapping canvas");

    const schemaCallsBefore = getMock.mock.calls.filter(
      ([path]) => typeof path === "string" && path.includes("/schema"),
    ).length;

    fireEvent.click(screen.getByRole("button", { name: "AI Suggested" }));
    fireEvent.click(screen.getByRole("button", { name: "Manual" }));

    const schemaCallsAfter = getMock.mock.calls.filter(
      ([path]) => typeof path === "string" && path.includes("/schema"),
    ).length;
    expect(schemaCallsAfter).toBe(schemaCallsBefore);
  });

  it("does not show the toggle at all for a published mapping", async () => {
    mockEndpoints(draftMapping({ status: "published", current_version_id: 3 }));
    render(<SchemaMapperPage />);
    await selectMapping("CRM to DW");

    await screen.findByLabelText("Schema mapping canvas");
    expect(screen.queryByRole("group", { name: "Schema mapper mode" })).not.toBeInTheDocument();
  });

  it("shows a background-completion indicator on AI Suggested when generation finishes while viewing Manual", async () => {
    const mapping = draftMapping();
    mockEndpoints(mapping);
    postMock.mockImplementation(async (path: string) => {
      if (path === `/api/v1/mappings/${mapping.id}/suggestions`) return { task_id: "task-1" };
      throw new Error(`unexpected POST ${path}`);
    });
    // Once the task resolves, re-fetching suggestions should see one pending item.
    const suggestion: AISuggestion = {
      id: 1, mapping_id: mapping.id,
      target_table: "customers", target_column: "email", target_type: "varchar",
      source_table: "raw_customers", source_column: "email", source_type: "varchar",
      confidence: 80, reason: null, components: null, suggested_transformation: null,
      transformation_note: null, status: "pending", created_at: "2026-07-22T00:00:00Z",
      decided_at: null, decided_by: null,
    };
    let suggestionsReady = false;
    getMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/auth/me") return { role: "admin", email: "admin@test.local" };
      if (path.startsWith("/api/v1/mappings/?")) return paginated([mapping]);
      if (path === `/api/v1/mappings/${mapping.id}`) return mapping;
      if (path.startsWith(`/api/v1/mappings/${mapping.id}/suggestions`)) {
        return paginated(suggestionsReady ? [suggestion] : []);
      }
      if (path === `/api/v1/connectors/${mapping.source_id}/schema`) return { schema: {} };
      if (path === `/api/v1/connectors/${mapping.target_id}/schema`) return { schema: {} };
      if (path === "/api/v1/workspace-layout/schema-mapper") return { workspace_key: "schema-mapper", layout: {} };
      if (path === `/api/v1/mappings/${mapping.id}/annotations`) return [];
      if (path === `/api/v1/mappings/${mapping.id}/versions`) return { items: [] };
      if (path === "/api/v1/tasks/task-1") {
        suggestionsReady = true;
        return { status: "SUCCESS", result: { suggestions_created: 1 } };
      }
      throw new Error(`unexpected GET ${path}`);
    });

    render(<SchemaMapperPage />);
    await selectMapping("CRM to DW");
    await screen.findByLabelText("Schema mapping canvas");

    fireEvent.click(screen.getByLabelText("Request AI suggestions"));

    await waitFor(
      () => expect(screen.getByRole("button", { name: "AI Suggested" })).toHaveTextContent("AI Suggested"),
      { timeout: 6000 },
    );
    await waitFor(
      () => expect(screen.getByLabelText("Completed in background")).toBeInTheDocument(),
      { timeout: 6000 },
    );
  });
});
