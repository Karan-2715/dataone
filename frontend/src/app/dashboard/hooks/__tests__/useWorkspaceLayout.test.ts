import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWorkspaceLayout } from "../useWorkspaceLayout";

const { getMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { get: getMock, put: putMock } }));

const DEFAULTS = { properties: { state: "normal" as const, width: 288 } };

describe("useWorkspaceLayout (Enterprise v2, E11-8)", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts with the caller's defaults before the fetch resolves", () => {
    getMock.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useWorkspaceLayout("schema-mapper", DEFAULTS));
    expect(result.current.panels).toEqual(DEFAULTS);
    expect(result.current.loaded).toBe(false);
  });

  it("merges the persisted layout over the defaults once loaded", async () => {
    getMock.mockResolvedValue({
      workspace_key: "schema-mapper",
      layout: { panels: { properties: { state: "collapsed", width: 340 } } },
    });
    const { result } = renderHook(() => useWorkspaceLayout("schema-mapper", DEFAULTS));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.panels.properties).toEqual({ state: "collapsed", width: 340 });
    expect(getMock).toHaveBeenCalledWith("/api/v1/workspace-layout/schema-mapper");
  });

  it("falls back to defaults silently when the fetch fails", async () => {
    getMock.mockRejectedValue(new Error("unreachable"));
    const { result } = renderHook(() => useWorkspaceLayout("schema-mapper", DEFAULTS));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.panels).toEqual(DEFAULTS);
  });

  it("debounces updatePanel saves through a single PUT", async () => {
    getMock.mockResolvedValue({ workspace_key: "schema-mapper", layout: {} });
    const { result } = renderHook(() => useWorkspaceLayout("schema-mapper", DEFAULTS));
    await waitFor(() => expect(result.current.loaded).toBe(true));

    vi.useFakeTimers();
    act(() => {
      result.current.updatePanel("properties", { width: 300 });
      result.current.updatePanel("properties", { width: 320 });
      result.current.updatePanel("properties", { width: 340 });
    });
    expect(result.current.panels.properties).toEqual({ state: "normal", width: 340 });
    expect(putMock).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(putMock).toHaveBeenCalledTimes(1);
    expect(putMock).toHaveBeenCalledWith("/api/v1/workspace-layout/schema-mapper", {
      layout: { panels: { properties: { state: "normal", width: 340 } } },
    });
  });
});
