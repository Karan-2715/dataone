"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { EmptyState, LoadingSpinner } from "./StateViews";

export interface GlobalSearchResult {
  kind: "connection" | "table" | "column";
  connection_id: number;
  connection_name: string;
  table_id: number | null;
  table_name: string | null;
  column_id: number | null;
  column_name: string | null;
  data_type: string | null;
}

interface GlobalSearchResponse {
  query: string;
  total: number;
  results: GlobalSearchResult[];
}

function resultName(result: GlobalSearchResult): string {
  return result.column_name ?? result.table_name ?? result.connection_name;
}

function resultHref(result: GlobalSearchResult): string {
  const params = new URLSearchParams({ connection_id: String(result.connection_id) });
  if (result.table_name) params.set("q", result.table_name);
  if (result.column_name) params.set("q", result.column_name);
  return `/dashboard/schema?${params.toString()}`;
}

export default function GlobalSearchPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GlobalSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen(true);
      } else if (event.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!open) return;
    const focusTimer = window.setTimeout(() => inputRef.current?.focus(), 0);
    const trigger = triggerRef.current;
    return () => {
      window.clearTimeout(focusTimer);
      // Focus lands on the input via a programmatic timer, not a real Tab
      // keypress, so browsers can't reliably tell it's keyboard-driven —
      // returning focus to the trigger keeps keyboard users from losing
      // their place when the dialog closes (APG dialog pattern).
      trigger?.focus();
    };
  }, [open]);

  // Basic focus trap: Tab/Shift+Tab cycles within the dialog instead of
  // escaping into the page behind the backdrop (APG modal dialog pattern —
  // the result list is dynamic, so focusables are queried live on each key).
  const onDialogKeyDown = (event: React.KeyboardEvent) => {
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusables = Array.from(
      dialogRef.current.querySelectorAll<HTMLElement>(
        'a[href], button, input, [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((el) => !el.hasAttribute("disabled"));
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  useEffect(() => {
    const trimmed = query.trim();
    if (!open || trimmed.length < 2) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError("");
      api.get<GlobalSearchResponse>(
        `/api/v1/search?q=${encodeURIComponent(trimmed)}&page_size=20`,
        { signal: controller.signal },
      ).then((response) => setResults(response.results))
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
          setResults([]);
          setError(err instanceof Error ? err.message : "Search failed.");
        })
        .finally(() => setLoading(false));
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, query]);

  return (
    <>
      <button ref={triggerRef} type="button" className="hidden md:flex items-center gap-2 px-3 py-1.5 rounded-xl bg-surface-overlay border border-border text-xs text-fg-subtle hover:border-border-strong transition-colors min-w-[210px]" onClick={() => setOpen(true)} aria-label="Search connections, tables, and columns" aria-expanded={open} aria-controls="global-search-dialog" aria-haspopup="dialog">
        <span aria-hidden="true">🔍</span>
        <span className="flex-1 text-left">Search catalog…</span>
        <kbd className="text-[10px] bg-surface px-1.5 py-0.5 rounded font-mono border border-border">⌘K</kbd>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 px-4 pt-[12vh]" onMouseDown={() => setOpen(false)}>
          <section id="global-search-dialog" ref={dialogRef} role="dialog" aria-modal="true" aria-label="Global catalog search" className="glass-strong w-full max-w-2xl overflow-hidden rounded-2xl border border-border-strong shadow-2xl" onMouseDown={(event) => event.stopPropagation()} onKeyDown={onDialogKeyDown}>
            <div className="flex items-center gap-3 border-b border-border p-4">
              <span aria-hidden="true">🔍</span>
              <input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search connections, tables, and columns" aria-label="Search query" className="min-w-0 flex-1 bg-transparent text-sm text-fg outline-none focus:ring-2 focus:ring-accent/50 rounded-lg placeholder:text-fg-subtle" />
              {loading && <LoadingSpinner size="sm" label="Searching" />}
              <button type="button" onClick={() => setOpen(false)} aria-label="Close search" className="rounded-lg px-2 py-1 text-xs text-fg-subtle hover:bg-surface-overlay">Esc</button>
            </div>
            <div className="max-h-[55vh] overflow-y-auto p-2">
              {error ? (
                <p className="p-4 text-sm text-red-500" role="alert">{error}</p>
              ) : query.trim().length < 2 ? (
                <p className="p-4 text-sm text-fg-subtle">Enter at least 2 characters.</p>
              ) : !loading && results.length === 0 ? (
                <EmptyState title="No catalog matches" description="Try a table, column, or connection name." />
              ) : results.map((result) => (
                <Link key={`${result.kind}-${result.column_id ?? result.table_id ?? result.connection_id}`} href={resultHref(result)} onClick={() => setOpen(false)} className="flex items-center gap-3 rounded-xl px-3 py-2.5 hover:bg-surface-overlay focus-visible:outline-2 focus-visible:outline-accent">
                  <span className="w-20 text-[10px] font-semibold uppercase tracking-wide text-fg-subtle">{result.kind}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-fg">{resultName(result)}</span>
                    <span className="block truncate text-xs text-fg-subtle">{[result.connection_name, result.table_name, result.data_type].filter(Boolean).join(" · ")}</span>
                  </span>
                </Link>
              ))}
            </div>
          </section>
        </div>
      )}
    </>
  );
}
