import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Canvas from "../components/Canvas";
import type { FieldMapping } from "../lib/types";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("@/lib/api", () => ({ api: { get: getMock } }));

function schemaOf(columns: Array<{ name: string; type?: string }>) {
  return { t1: columns.map((c) => ({ name: c.name, type: c.type ?? "TEXT" })) };
}

function mockMapping(sourceCols: string[], targetCols: string[]) {
  getMock.mockImplementation(async (path: string) => {
    if (path === "/api/v1/mappings/1") {
      return { id: 1, source_id: 10, target_id: 20 };
    }
    if (path === "/api/v1/connectors/10/schema") {
      return { schema: schemaOf(sourceCols.map((name) => ({ name }))) };
    }
    if (path === "/api/v1/connectors/20/schema") {
      return { schema: schemaOf(targetCols.map((name) => ({ name }))) };
    }
    throw new Error(`unexpected path in test: ${path}`);
  });
}

function baseProps(overrides: Partial<Parameters<typeof Canvas>[0]> = {}) {
  return {
    mappingId: 1,
    edges: [] as FieldMapping[],
    selectedEdgeId: null,
    canEdit: true,
    role: "admin" as const,
    onSelectEdge: vi.fn(),
    onCreateEdge: vi.fn().mockResolvedValue({ id: 99 } as FieldMapping),
    ...overrides,
  };
}

describe("Canvas keyboard accessibility (mapper_tasks/bugs #02)", () => {
  beforeEach(() => {
    getMock.mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it("makes a stageable source column focusable and toggles staging on Enter", async () => {
    mockMapping(["first_name"], ["full_name"]);
    render(<Canvas {...baseProps()} />);

    const row = await screen.findByRole("button", {
      name: /t1\.first_name\. Press Enter to stage/,
    });
    expect(row).toHaveAttribute("tabIndex", "0");
    expect(row).toHaveAttribute("aria-pressed", "false");

    fireEvent.keyDown(row, { key: "Enter" });

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /staged as source 1/ }),
      ).toBeInTheDocument(),
    );
  });

  it("also stages on Space, and unstages on a second Enter", async () => {
    mockMapping(["email"], ["contact_email"]);
    render(<Canvas {...baseProps()} />);

    const row = await screen.findByRole("button", { name: /t1\.email/ });
    expect(row).toHaveAttribute("aria-pressed", "false");

    fireEvent.keyDown(row, { key: " " });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /t1\.email/ })).toHaveAttribute(
        "aria-pressed", "true",
      ),
    );

    fireEvent.keyDown(screen.getByRole("button", { name: /t1\.email/ }), { key: "Enter" });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /t1\.email/ })).toHaveAttribute(
        "aria-pressed", "false",
      ),
    );
  });

  it("announces staging and connection events in the live region", async () => {
    const onCreateEdge = vi.fn().mockResolvedValue({ id: 5 } as FieldMapping);
    mockMapping(["first_name"], ["full_name"]);
    render(<Canvas {...baseProps({ onCreateEdge })} />);

    const source = await screen.findByRole("button", { name: /t1\.first_name/ });
    fireEvent.keyDown(source, { key: "Enter" });

    const live = document.querySelector('[role="status"]');
    await waitFor(() => expect(live?.textContent).toMatch(/first_name staged as source 1/));

    const target = screen.getByRole("button", { name: /Connect 1 staged source/ });
    await act(async () => {
      fireEvent.keyDown(target, { key: "Enter" });
    });
    await waitFor(() =>
      expect(live?.textContent).toMatch(/Connected t1\.first_name to t1\.full_name/),
    );
  });

  it("a non-actionable (already-mapped) row is not in the tab order", async () => {
    mockMapping(["first_name", "last_name"], ["full_name"]);
    const edges: FieldMapping[] = [
      {
        id: 1, mapping_id: 1,
        target: { table: "t1", column: "full_name" },
        sources: [{ table: "t1", column: "first_name" }],
        transformation: { kind: "direct" }, origin: "manual", audit: {},
        created_at: "", updated_at: "",
      },
    ];
    render(<Canvas {...baseProps({ edges })} />);

    await screen.findByText("last_name");
    // first_name is already a mapped source — no button role, no tab stop.
    expect(screen.queryByRole("button", { name: /t1\.first_name/ })).not.toBeInTheDocument();
  });

  it("does not expose keyboard/button semantics when canEdit is false", async () => {
    mockMapping(["first_name"], ["full_name"]);
    render(<Canvas {...baseProps({ canEdit: false })} />);

    await screen.findByText("first_name");
    expect(screen.queryByRole("button", { name: /first_name/ })).not.toBeInTheDocument();
  });
});

describe("Canvas manual-mapping target click feedback (uiux bug report: \"target is not choosing the target columns\")", () => {
  beforeEach(() => {
    getMock.mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it("gives a guided hint instead of silently no-oping when a target is clicked before any source is staged", async () => {
    mockMapping(["first_name"], ["full_name"]);
    const onHint = vi.fn();
    const onCreateEdge = vi.fn();
    render(<Canvas {...baseProps({ onHint, onCreateEdge })} />);

    const target = await screen.findByRole("button", {
      name: /t1\.full_name\. Stage a source column first/,
    });
    fireEvent.click(target);

    expect(onHint).toHaveBeenCalledWith(
      "Select a source column first, then click this target to connect it.",
    );
    expect(onCreateEdge).not.toHaveBeenCalled();
  });

  it("connects immediately with no hint once a source is staged", async () => {
    mockMapping(["first_name"], ["full_name"]);
    const onHint = vi.fn();
    const onCreateEdge = vi.fn().mockResolvedValue({ id: 1 });
    render(<Canvas {...baseProps({ onHint, onCreateEdge })} />);

    const source = await screen.findByRole("button", { name: /t1\.first_name/ });
    fireEvent.click(source);

    const target = await screen.findByRole("button", { name: /Connect 1 staged source/ });
    fireEvent.click(target);

    await waitFor(() => expect(onCreateEdge).toHaveBeenCalled());
    expect(onHint).not.toHaveBeenCalled();
  });

  it("does not expose the hint affordance on an already-mapped target", async () => {
    mockMapping(["first_name"], ["full_name"]);
    const edges: FieldMapping[] = [
      {
        id: 1, mapping_id: 1,
        target: { table: "t1", column: "full_name" },
        sources: [{ table: "t1", column: "first_name" }],
        transformation: { kind: "direct" }, origin: "manual", audit: {},
        created_at: "", updated_at: "",
      },
    ];
    const onHint = vi.fn();
    render(<Canvas {...baseProps({ edges, onHint })} />);

    await screen.findByText("full_name");
    expect(
      screen.queryByRole("button", { name: /full_name\. Stage a source column first/ }),
    ).not.toBeInTheDocument();
  });
});

describe("Canvas virtualization (mapper_tasks/bugs #04)", () => {
  beforeEach(() => {
    getMock.mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it("renders every row for a small schema (unchanged below the viewport cap)", async () => {
    const cols = Array.from({ length: 5 }, (_, i) => `col_${i}`);
    mockMapping(cols, ["target_col"]);
    render(<Canvas {...baseProps()} />);

    await screen.findByText("col_0");
    for (const c of cols) {
      expect(screen.getByText(c)).toBeInTheDocument();
    }
  });

  it("windows a large schema instead of mounting every row", async () => {
    const cols = Array.from({ length: 200 }, (_, i) => `col_${String(i).padStart(3, "0")}`);
    mockMapping(cols, ["target_col"]);
    render(<Canvas {...baseProps()} />);

    await screen.findByText("col_000");
    // The bounded panel viewport (<=560px at 36px rows) fits well under 200
    // rows — react-window must not have mounted all 200 into the DOM.
    const mountedSourceRows = cols.filter((c) => screen.queryByText(c) !== null);
    expect(mountedSourceRows.length).toBeLessThan(cols.length);
    expect(mountedSourceRows.length).toBeGreaterThan(0);
    // The far end of the list is truly virtualized away, not just hidden.
    expect(screen.queryByText("col_199")).not.toBeInTheDocument();
  });
});
