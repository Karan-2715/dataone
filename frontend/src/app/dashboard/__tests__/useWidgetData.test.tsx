import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useWidgetData } from "../hooks/useWidgetData";

describe("useWidgetData", () => {
  it("resolves data and clears loading on success", async () => {
    const { result } = renderHook(() =>
      useWidgetData(() => Promise.resolve({ ok: true }), []),
    );
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toEqual({ ok: true });
    expect(result.current.isError).toBe(false);
  });

  it("captures the error message on failure", async () => {
    const { result } = renderHook(() =>
      useWidgetData(() => Promise.reject(new Error("fetch failed")), []),
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.errorMessage).toBe("fetch failed");
    expect(result.current.data).toBeNull();
  });

  it("passes an AbortSignal to the fetcher (bugs #02)", async () => {
    const fetcher = vi.fn((signal: AbortSignal) => {
      expect(signal).toBeInstanceOf(AbortSignal);
      return Promise.resolve("ok");
    });
    const { result } = renderHook(() => useWidgetData(fetcher, []));
    await waitFor(() => expect(result.current.data).toBe("ok"));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("aborts the in-flight request's signal when a newer call supersedes it (bugs #02)", async () => {
    let firstSignal: AbortSignal | undefined;
    let resolveFirst!: (v: string) => void;
    const first = new Promise<string>((r) => (resolveFirst = r));
    let call = 0;
    const fetcher = vi.fn((signal: AbortSignal) => {
      call += 1;
      if (call === 1) {
        firstSignal = signal;
        return first;
      }
      return Promise.resolve("second");
    });

    const { result } = renderHook(() => useWidgetData(fetcher, []));
    expect(firstSignal?.aborted).toBe(false);

    await act(async () => {
      await result.current.refetch(); // supersedes the in-flight first request
    });
    await waitFor(() => expect(result.current.data).toBe("second"));

    expect(firstSignal?.aborted).toBe(true); // the superseded request was cancelled, not just ignored
    resolveFirst("first"); // even if it resolves late, it's already aborted+dropped
  });

  it("aborts the in-flight request on unmount", async () => {
    let capturedSignal: AbortSignal | undefined;
    const fetcher = vi.fn(
      (signal: AbortSignal) =>
        new Promise<string>(() => {
          capturedSignal = signal; // never resolves — simulates a slow request
        }),
    );
    const { unmount } = renderHook(() => useWidgetData(fetcher, []));
    unmount();
    expect(capturedSignal?.aborted).toBe(true);
  });

  it("recovers via refetch after an error", async () => {
    let fail = true;
    const fetcher = vi.fn(() =>
      fail ? Promise.reject(new Error("boom")) : Promise.resolve("fine"),
    );
    const { result } = renderHook(() => useWidgetData(fetcher, []));
    await waitFor(() => expect(result.current.isError).toBe(true));

    fail = false;
    // refetch returns a promise — act must be awaited or React's act scope
    // stays open and leaks into subsequent tests.
    await act(async () => {
      await result.current.refetch();
    });
    await waitFor(() => expect(result.current.data).toBe("fine"));
    expect(result.current.isError).toBe(false);
    expect(result.current.errorMessage).toBeUndefined();
  });

  it("drops stale responses when a newer request supersedes them (latest wins)", async () => {
    let resolveFirst!: (v: string) => void;
    const first = new Promise<string>((r) => (resolveFirst = r));
    let call = 0;
    const fetcher = vi.fn(() => (++call === 1 ? first : Promise.resolve("second")));

    const { result } = renderHook(() => useWidgetData(fetcher, []));
    await act(async () => {
      await result.current.refetch(); // supersedes the in-flight first request
    });
    await waitFor(() => expect(result.current.data).toBe("second"));

    resolveFirst("first"); // slow response lands late…
    await new Promise((r) => setTimeout(r, 10));
    expect(result.current.data).toBe("second"); // …and must not overwrite
  });

  it("refetches when deps change", async () => {
    const fetcher = vi.fn((dep: string) => Promise.resolve(dep));
    const { result, rerender } = renderHook(
      ({ dep }) => useWidgetData(() => fetcher(dep), [dep]),
      { initialProps: { dep: "24h" } },
    );
    await waitFor(() => expect(result.current.data).toBe("24h"));
    rerender({ dep: "7d" });
    await waitFor(() => expect(result.current.data).toBe("7d"));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
