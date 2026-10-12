"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export interface UseWidgetDataResult<T> {
  data: T | null;
  isLoading: boolean;
  isError: boolean;
  errorMessage: string | undefined;
  refetch: () => void;
}

/**
 * Per-widget fetch state (dashboard_tasks #3). Each caller gets isolated
 * loading/error state so one failing widget never degrades another (FR6).
 *
 * A superseding call (refetch, or deps changing) aborts the previous
 * in-flight request via AbortController — the fetcher receives the signal
 * and is expected to forward it to `api.get(path, { signal })` — instead of
 * just discarding its result once it lands (dashboard_tasks/bugs #02: the
 * prior "latest wins" counter was correct for the UI but let a superseded
 * request keep consuming client and backend resources to completion for
 * nothing). The request-counter guard stays as a second, cheaper check for
 * any fetcher that doesn't wire the signal through.
 */
export function useWidgetData<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[] = [],
): UseWidgetDataResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | undefined>();
  const requestSeq = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const seq = ++requestSeq.current;
    setIsLoading(true);
    setIsError(false);
    setErrorMessage(undefined);
    try {
      const result = await fetcher(controller.signal);
      if (seq !== requestSeq.current) return; // superseded — drop
      setData(result);
    } catch (err) {
      if (seq !== requestSeq.current) return;
      if (err instanceof DOMException && err.name === "AbortError") return;
      setIsError(true);
      setErrorMessage(err instanceof Error ? err.message : "An error occurred");
    } finally {
      if (seq === requestSeq.current) setIsLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deps are forwarded by the caller; fetcher is intentionally re-read on each run
  }, deps);

  useEffect(() => {
    load();
    return () => controllerRef.current?.abort();
  }, [load]);

  return { data, isLoading, isError, errorMessage, refetch: load };
}
