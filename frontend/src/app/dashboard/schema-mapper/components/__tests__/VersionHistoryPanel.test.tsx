import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import VersionHistoryPanel from "../VersionHistoryPanel";
import { ApiError } from "@/lib/api";
import type { VersionDiffResponse, VersionListResponse, VersionSummary } from "../../lib/types";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { get: getMock, post: postMock },
  ApiError: class ApiError extends Error {
    constructor(
      public readonly status: number,
      message: string,
    ) {
      super(message);
    }
  },
}));

function makeVersion(overrides: Partial<VersionSummary> = {}): VersionSummary {
  return {
    id: 1,
    version_number: 1,
    status: "published",
    published_at: "2026-07-20T00:00:00Z",
    published_by: "admin@test.local",
    edge_count: 1,
    is_current: false,
    ...overrides,
  };
}

function mockVersionsList(items: VersionSummary[]) {
  getMock.mockImplementation((path: string) => {
    if (path.includes("/versions/diff")) {
      // The panel auto-fires a diff request whenever >=2 versions load;
      // tests that don't assert on diff content just need this to resolve
      // harmlessly rather than reject or throw.
      return Promise.resolve({
        mapping_id: 1,
        from_version: items[items.length - 1],
        to_version: items[0],
        added: [],
        removed: [],
        changed: [],
        unchanged_count: 0,
      } satisfies VersionDiffResponse);
    }
    return Promise.resolve({ items } satisfies VersionListResponse);
  });
}

describe("VersionHistoryPanel (Enterprise v2, E13-5/6)", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("shows an honest empty state with no published versions", async () => {
    mockVersionsList([]);
    render(<VersionHistoryPanel mappingId={1} role="admin" onRolledBack={vi.fn()} />);
    expect(await screen.findByText("No published versions yet.")).toBeInTheDocument();
  });

  it("marks the current version and lists edge counts", async () => {
    mockVersionsList([makeVersion({ id: 2, version_number: 2, is_current: true, edge_count: 3 })]);
    render(<VersionHistoryPanel mappingId={1} role="admin" onRolledBack={vi.fn()} />);
    expect(await screen.findByText("v2")).toBeInTheDocument();
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText(/3 fields/)).toBeInTheDocument();
  });

  it("does not offer Rollback on the current version", async () => {
    mockVersionsList([makeVersion({ id: 2, version_number: 2, is_current: true })]);
    render(<VersionHistoryPanel mappingId={1} role="admin" onRolledBack={vi.fn()} />);
    await screen.findByText("v2");
    expect(screen.queryByRole("button", { name: /Roll back to v2/ })).not.toBeInTheDocument();
  });

  it("hides Rollback for a viewer role", async () => {
    mockVersionsList([
      makeVersion({ id: 2, version_number: 2, is_current: true }),
      makeVersion({ id: 1, version_number: 1, is_current: false }),
    ]);
    render(<VersionHistoryPanel mappingId={1} role="viewer" onRolledBack={vi.fn()} />);
    await within(await screen.findByRole("list")).findByText("v2");
    expect(screen.queryByRole("button", { name: /Roll back to v1/ })).not.toBeInTheDocument();
  });

  it("auto-compares the previous version against the current one", async () => {
    getMock.mockImplementation((path: string) => {
      if (path.includes("/versions/diff")) {
        return Promise.resolve({
          mapping_id: 1,
          from_version: makeVersion({ id: 1, version_number: 1 }),
          to_version: makeVersion({ id: 2, version_number: 2, is_current: true }),
          added: [{ target: { table: "t1", column: "c2" }, sources: [], transformation: { kind: "direct" }, origin: "manual" }],
          removed: [],
          changed: [],
          unchanged_count: 1,
        } satisfies VersionDiffResponse);
      }
      return Promise.resolve({
        items: [
          makeVersion({ id: 2, version_number: 2, is_current: true }),
          makeVersion({ id: 1, version_number: 1, is_current: false }),
        ],
      } satisfies VersionListResponse);
    });
    render(<VersionHistoryPanel mappingId={1} role="admin" onRolledBack={vi.fn()} />);
    await within(await screen.findByRole("list")).findByText("v2");
    await waitFor(() => expect(screen.getByText(/\+1 added/)).toBeInTheDocument());
    expect(screen.getByText("+ t1.c2")).toBeInTheDocument();
  });

  it("rolls back to an older version after confirmation and notifies the parent", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockVersionsList([
      makeVersion({ id: 2, version_number: 2, is_current: true }),
      makeVersion({ id: 1, version_number: 1, is_current: false }),
    ]);
    postMock.mockResolvedValue({});
    const onRolledBack = vi.fn();
    render(<VersionHistoryPanel mappingId={7} role="admin" onRolledBack={onRolledBack} />);
    await within(await screen.findByRole("list")).findByText("v2");

    fireEvent.click(screen.getByRole("button", { name: "Roll back to v1" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/api/v1/mappings/7/versions/1/rollback", {}),
    );
    await waitFor(() => expect(onRolledBack).toHaveBeenCalled());
  });

  it("does not roll back when the confirmation is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    mockVersionsList([
      makeVersion({ id: 2, version_number: 2, is_current: true }),
      makeVersion({ id: 1, version_number: 1, is_current: false }),
    ]);
    render(<VersionHistoryPanel mappingId={7} role="admin" onRolledBack={vi.fn()} />);
    await within(await screen.findByRole("list")).findByText("v2");
    fireEvent.click(screen.getByRole("button", { name: "Roll back to v1" }));
    expect(postMock).not.toHaveBeenCalled();
  });

  it("shows an error message when rollback fails, without crashing", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockVersionsList([
      makeVersion({ id: 2, version_number: 2, is_current: true }),
      makeVersion({ id: 1, version_number: 1, is_current: false }),
    ]);
    postMock.mockRejectedValue(new ApiError(409, "mapping is not published"));
    render(<VersionHistoryPanel mappingId={7} role="admin" onRolledBack={vi.fn()} />);
    await within(await screen.findByRole("list")).findByText("v2");
    fireEvent.click(screen.getByRole("button", { name: "Roll back to v1" }));
    expect(await screen.findByText("mapping is not published")).toBeInTheDocument();
  });
});
