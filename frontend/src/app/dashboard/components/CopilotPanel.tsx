"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { LoadingSpinner } from "./StateViews";

interface ConnectorRef {
  id: number;
  name: string;
}

interface AskResponse {
  session_id: string;
  summary: string | null;
  error: string | null;
  intent: string;
  sql: string | null;
  rows: Record<string, unknown>[];
  row_count: number;
  platform_insight: Record<string, unknown> | null;
}

interface Turn {
  role: "user" | "assistant";
  content: string;
  intent?: string;
}

const INTENT_LABELS: Record<string, string> = {
  platform_insight: "Platform Insight",
  schema_design: "Schema Design",
  external_action: "External Action",
  read_query: "Read Query",
  ambiguous: "Ambiguous",
};

/**
 * Persistent AI Copilot side panel (Enterprise v2, E09) — a single
 * conversational surface available from any dashboard page, routed
 * through AskData's existing intent gate (read_query / schema_design /
 * external_action / platform_insight). This is a router UI over engines
 * that already exist; it does not add a new backend surface itself.
 */
export default function CopilotPanel() {
  const [open, setOpen] = useState(false);
  const [connections, setConnections] = useState<ConnectorRef[]>([]);
  const [connectionId, setConnectionId] = useState<number | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open || connections.length > 0) return;
    api.get<ConnectorRef[]>("/api/v1/connectors/")
      .then((rows) => {
        setConnections(rows);
        if (rows.length > 0) setConnectionId((cur) => cur ?? rows[0].id);
      })
      .catch(() => setConnections([]));
  }, [open, connections.length]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    const onClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        close();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onClickOutside);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onClickOutside);
    };
  }, [open, close]);

  useEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    const timer = window.setTimeout(() => {
      dialogRef.current?.querySelector<HTMLElement>("select, input, button")?.focus();
    }, 0);
    return () => {
      window.clearTimeout(timer);
      trigger?.focus();
    };
  }, [open]);

  const onDialogKeyDown = (event: React.KeyboardEvent) => {
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusables = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
      'select, input, button, a[href], [tabindex]:not([tabindex="-1"])',
    )).filter((element) => !element.hasAttribute("disabled"));
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

  const changeConnection = (nextId: number) => {
    setConnectionId(nextId);
    setSessionId(undefined);
    setTurns([]);
  };

  // build-validation B-E09-12: without this, the session_id (and the
  // turn history sent via _augment_with_history) persists for the whole
  // panel session — a prior unrelated question keeps biasing generation
  // with no way to reset except closing the panel, which doesn't clear
  // this state either.
  const newConversation = () => {
    setSessionId(undefined);
    setTurns([]);
    setInput("");
  };

  const ask = async () => {
    const question = input.trim();
    if (!question || connectionId == null || loading) return;
    setTurns((t) => [...t, { role: "user", content: question }]);
    setInput("");
    setLoading(true);
    try {
      const data = await api.post<AskResponse>("/api/v1/askdata/ask", {
        connection_id: connectionId, question, session_id: sessionId,
      });
      setSessionId(data.session_id);
      setTurns((t) => [...t, {
        role: "assistant",
        content: data.error || data.summary || "No response generated.",
        intent: data.intent,
      }]);
    } catch (err) {
      setTurns((t) => [...t, {
        role: "assistant",
        content: err instanceof Error ? err.message : "The copilot is temporarily unavailable.",
      }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Open AI Copilot"
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls="copilot-panel"
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-accent-soft text-accent border border-accent/30 text-xs font-semibold hover:bg-accent/20 transition-colors"
      >
        <span aria-hidden="true">✨</span>
        Copilot
      </button>

      {open && (
        <section
          ref={dialogRef}
          id="copilot-panel"
          role="dialog"
          aria-modal="true"
          aria-label="AI Copilot"
          onKeyDown={onDialogKeyDown}
          className="glass-strong absolute right-0 top-11 z-40 flex h-[32rem] w-96 flex-col overflow-hidden rounded-2xl border border-border-strong shadow-2xl"
        >
          <div className="flex items-center justify-between gap-2 border-b border-border p-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-fg">AI Copilot</h2>
              {turns.length > 0 && (
                <button
                  type="button"
                  onClick={newConversation}
                  aria-label="Start new conversation"
                  title="Clear this conversation and start a new one"
                  className="rounded-lg px-1.5 py-0.5 text-[10px] font-semibold text-fg-subtle hover:bg-surface-overlay hover:text-fg-muted"
                >
                  New
                </button>
              )}
            </div>
            {connections.length > 0 && (
              <select
                value={connectionId ?? ""}
                onChange={(e) => changeConnection(Number(e.target.value))}
                aria-label="Copilot connection context"
                className="rounded-lg border border-border-strong bg-surface-overlay px-1.5 py-1 text-[10px] font-semibold text-fg-muted focus:outline-none focus:ring-2 focus:ring-accent/50"
              >
                {connections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            )}
          </div>

          <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
            {turns.length === 0 && (
              <p className="text-xs text-fg-subtle">
                Ask about your data (&ldquo;show me all customers&rdquo;), request a design (&ldquo;create a target schema for…&rdquo;),
                or ask about the platform itself (&ldquo;what are the critical risks?&rdquo;, &ldquo;what&rsquo;s my data quality score?&rdquo;).
              </p>
            )}
            {turns.map((turn, i) => (
              <div
                key={i}
                className={`rounded-xl px-3 py-2 text-xs ${
                  turn.role === "user"
                    ? "self-end bg-accent-soft text-fg"
                    : "self-start bg-surface-overlay text-fg-muted"
                }`}
              >
                {turn.intent && turn.role === "assistant" && (
                  <div className="mb-1 text-[9px] font-semibold uppercase tracking-wide text-fg-subtle">
                    {INTENT_LABELS[turn.intent] ?? turn.intent}
                  </div>
                )}
                {turn.content}
              </div>
            ))}
            {loading && <LoadingSpinner size="sm" label="Thinking" />}
          </div>

          <form
            onSubmit={(e) => { e.preventDefault(); void ask(); }}
            className="flex items-center gap-2 border-t border-border p-3"
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask the copilot…"
              disabled={connectionId == null}
              className="min-w-0 flex-1 rounded-lg border border-border-strong bg-surface-overlay px-2 py-1.5 text-xs text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent/50 disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={connectionId == null || loading || !input.trim()}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg disabled:opacity-50"
            >
              Ask
            </button>
          </form>
        </section>
      )}
    </div>
  );
}
