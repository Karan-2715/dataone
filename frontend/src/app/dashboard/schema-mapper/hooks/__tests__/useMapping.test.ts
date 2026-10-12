import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMapping } from "../useMapping";
import { ApiError as MockApiError } from "@/lib/api";
import type { Mapping, Paginated, AISuggestion } from "../../lib/types";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));

vi.mock("@/lib/api", () => ({
  api: { get: getMock, post: postMock },
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

function baseMapping(overrides: Partial<Mapping> = {}): Mapping {
  return {
    id: 1,
    name: "Retail sync",
    source_id: 10,
    target_id: 20,
    status: "draft",
    review_stage: "draft",
    current_version_id: null,
    created_by: "admin@test.local",
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
    edges: [],
    ...overrides,
  };
}

function emptySuggestions(): Paginated<AISuggestion> {
  return { items: [], total: 0, limit: 200, offset: 0, has_more: false };
}

describe("useMapping — AI suggestion idempotency (uiux bug report)", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    getMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/auth/me") return { role: "admin", email: "admin@test.local" };
      if (path === "/api/v1/mappings/1") return baseMapping();
      if (path.startsWith("/api/v1/mappings/1/suggestions")) return emptySuggestions();
      throw new Error(`unexpected GET ${path}`);
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("does not enqueue a second task while a request is already generating", async () => {
    postMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/mappings/1/suggestions") return { task_id: "task-1" };
      throw new Error(`unexpected POST ${path}`);
    });
    const { result } = renderHook(() => useMapping());
    await act(async () => {
      await result.current.load(1);
    });
    expect(result.current.mapping?.id).toBe(1);

    // Two clicks back-to-back, before either has a chance to resolve.
    act(() => {
      void result.current.requestSuggestions();
      void result.current.requestSuggestions();
    });

    expect(postMock).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(result.current.generatingSuggestions).toBe(true));
  });

  it("re-enables the button once the task reaches a terminal status", async () => {
    postMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/mappings/1/suggestions") return { task_id: "task-1" };
      throw new Error(`unexpected POST ${path}`);
    });
    getMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/auth/me") return { role: "admin", email: "admin@test.local" };
      if (path === "/api/v1/mappings/1") return baseMapping();
      if (path.startsWith("/api/v1/mappings/1/suggestions")) return emptySuggestions();
      if (path === "/api/v1/tasks/task-1") {
        return { status: "SUCCESS", result: { suggestions_created: 3 } };
      }
      throw new Error(`unexpected GET ${path}`);
    });

    const { result } = renderHook(() => useMapping());
    await act(async () => {
      await result.current.load(1);
    });

    await act(async () => {
      await result.current.requestSuggestions();
    });
    expect(result.current.generatingSuggestions).toBe(true);

    // The hook polls on a timer (1.5s initial delay) rather than resolving
    // inline, so wait for the poll to actually run and observe SUCCESS.
    await waitFor(() => expect(result.current.generatingSuggestions).toBe(false), {
      timeout: 5000,
    });

    // Once cleared, a fresh request is allowed to enqueue again.
    act(() => {
      void result.current.requestSuggestions();
    });
    expect(postMock).toHaveBeenCalledTimes(2);
  });

  it("resets the guard when switching mappings so the new mapping isn't stuck", async () => {
    postMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/mappings/1/suggestions") return { task_id: "task-1" };
      throw new Error(`unexpected POST ${path}`);
    });
    getMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/auth/me") return { role: "admin", email: "admin@test.local" };
      if (path === "/api/v1/mappings/1") return baseMapping({ id: 1 });
      if (path === "/api/v1/mappings/2") return baseMapping({ id: 2 });
      if (path.startsWith("/api/v1/mappings/1/suggestions")) return emptySuggestions();
      if (path.startsWith("/api/v1/mappings/2/suggestions")) return emptySuggestions();
      throw new Error(`unexpected GET ${path}`);
    });

    const { result } = renderHook(() => useMapping());
    await act(async () => {
      await result.current.load(1);
    });
    act(() => {
      void result.current.requestSuggestions();
    });
    expect(result.current.generatingSuggestions).toBe(true);

    await act(async () => {
      await result.current.load(2);
    });
    expect(result.current.generatingSuggestions).toBe(false);

    // Mapping 2's own request must be allowed to enqueue immediately.
    act(() => {
      void result.current.requestSuggestions();
    });
    expect(postMock).toHaveBeenCalledWith("/api/v1/mappings/2/suggestions", {});
  });
});

describe("useMapping — publish() (uiux bug report: publish dialog looks stuck)", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    getMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/auth/me") return { role: "admin", email: "admin@test.local" };
      if (path === "/api/v1/mappings/1") return baseMapping();
      if (path.startsWith("/api/v1/mappings/1/suggestions")) return emptySuggestions();
      throw new Error(`unexpected GET ${path}`);
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("silently refreshes validation state when publish fails with a blocking-validation 422", async () => {
    let validateCalls = 0;
    postMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/mappings/1/publish") {
        throw new MockApiError(
          422,
          "target is NOT NULL but source is nullable and no null-handling transform provided",
        );
      }
      if (path === "/api/v1/mappings/1/validate") {
        validateCalls += 1;
        return { mapping_id: 1, ok_count: 1, warning_count: 0, blocking_count: 2, issues: [] };
      }
      throw new Error(`unexpected POST ${path}`);
    });

    const { result } = renderHook(() => useMapping());
    await act(async () => {
      await result.current.load(1);
    });
    expect(result.current.validation).toBeNull();

    await act(async () => {
      await expect(result.current.publish()).rejects.toThrow();
    });

    expect(validateCalls).toBe(1);
    expect(result.current.validation?.blocking_count).toBe(2);
  });

  it("does not touch validation state on a non-422 publish failure", async () => {
    let validateCalls = 0;
    postMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/mappings/1/publish") {
        throw new MockApiError(500, "schema snapshot failed: connector unreachable");
      }
      if (path === "/api/v1/mappings/1/validate") {
        validateCalls += 1;
        return { mapping_id: 1, ok_count: 1, warning_count: 0, blocking_count: 0, issues: [] };
      }
      throw new Error(`unexpected POST ${path}`);
    });

    const { result } = renderHook(() => useMapping());
    await act(async () => {
      await result.current.load(1);
    });

    await act(async () => {
      await expect(result.current.publish()).rejects.toThrow();
    });

    expect(validateCalls).toBe(0);
    expect(result.current.validation).toBeNull();
  });

  it("clearValidation resets validation to null", async () => {
    postMock.mockImplementation(async (path: string) => {
      if (path === "/api/v1/mappings/1/validate") {
        return { mapping_id: 1, ok_count: 3, warning_count: 0, blocking_count: 0, issues: [] };
      }
      throw new Error(`unexpected POST ${path}`);
    });
    const { result } = renderHook(() => useMapping());
    await act(async () => {
      await result.current.load(1);
    });
    await act(async () => {
      await result.current.validate();
    });
    expect(result.current.validation).not.toBeNull();

    act(() => {
      result.current.clearValidation();
    });
    expect(result.current.validation).toBeNull();
  });
});
