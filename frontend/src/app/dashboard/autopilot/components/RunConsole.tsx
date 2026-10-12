"use client";

import { useState, useEffect, useRef, ReactNode } from "react";
import { api } from "@/lib/api";

interface Connector {
  id: number;
  name: string;
  type: string;
}

interface LogEntry {
  step: string;
  message: string;
  level: string;
  created_at: string;
}

interface RunLogsResponse {
  run_id: string;
  status: string;
  logs: LogEntry[];
}

interface RunResponse {
  run_id?: string;
  status: string;
  recommendation_id?: number;
  already_pending?: boolean;
}

const STEP_ICONS: Record<string, ReactNode> = {
  init: <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" /></svg>,
  schema: "🔍",
  matching: <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" /></svg>,
  diff: <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>,
  security: " ",
  sql: "💾",
  execute: "🚀",
  complete: "✅",
  error: "❌",
};

interface RunConsoleProps {
  /** Execute mode now queues for approval (FR3); the page switches to the
      Approvals tab when that happens. */
  onQueuedForApproval: (recommendationId: number, alreadyPending: boolean) => void;
}

export default function RunConsole({ onQueuedForApproval }: RunConsoleProps) {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [sourceId, setSourceId] = useState<string>("");
  const [targetId, setTargetId] = useState<string>("");
  const [mode, setMode] = useState<"suggest" | "execute">("suggest");
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const consoleRef = useRef<HTMLDivElement>(null);
  const pollingRef = useRef<boolean>(false);

  useEffect(() => {
    api.get<Connector[]>("/api/v1/connectors/").then(setConnectors).catch(() => {});
  }, []);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      pollingRef.current = false;
    };
  }, []);

  function scrollConsole() {
    if (consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }

  async function poll(rid: string) {
    if (!pollingRef.current) return;
    try {
      const data = await api.get<RunLogsResponse>(`/api/v1/autopilot/runs/${rid}/logs`);
      setLogs(data.logs);
      setStatus(data.status);
      scrollConsole();
      if (data.status === "running" && pollingRef.current) {
        setTimeout(() => poll(rid), 2000);
      } else {
        pollingRef.current = false;
        setRunning(false);
      }
    } catch {
      pollingRef.current = false;
      setRunning(false);
    }
  }

  const handleRun = async () => {
    if (!sourceId || !targetId) return;
    setError("");
    setLogs([]);
    setStatus("running");
    setRunning(true);
    pollingRef.current = false; // stop any previous poll
    try {
      const res = await api.post<RunResponse>("/api/v1/autopilot/run", {
        source_id: Number(sourceId),
        target_id: Number(targetId),
        mode,
        model: "llama3",
      });
      if (res.status === "queued_for_approval" && res.recommendation_id != null) {
        // Execute mode is governed: it entered the approval queue instead of
        // running. Hand off to the Approvals tab.
        setStatus("");
        setRunning(false);
        onQueuedForApproval(res.recommendation_id, res.already_pending ?? false);
        return;
      }
      if (res.run_id) {
        setRunId(res.run_id);
        pollingRef.current = true;
        const rid = res.run_id;
        setTimeout(() => poll(rid), 1000);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to start autopilot";
      setError(msg);
      setStatus("");
      setRunning(false);
    }
  };

  const handleStop = () => {
    pollingRef.current = false;
    setRunning(false);
    setStatus("stopped");
  };

  const sourceName = connectors.find((c) => String(c.id) === sourceId)?.name ?? "";
  const targetName = connectors.find((c) => String(c.id) === targetId)?.name ?? "";

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Console log area */}
      <div className="lg:col-span-2 flex flex-col gap-4 rounded-2xl bg-surface border border-border p-6 backdrop-blur-sm">
        <div className="flex justify-between items-center border-b border-border pb-4">
          <h3 className="font-semibold text-fg-muted">AI Execution Console</h3>
          {running ? (
            <span className="flex items-center gap-1.5 text-xs text-blue-400 bg-blue-500/10 px-3 py-1 rounded-full border border-white/10 animate-pulse">
              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full" /> Running
            </span>
          ) : status === "completed" ? (
            <span className="flex items-center gap-1.5 text-xs text-emerald-400 bg-emerald-500/10 px-3 py-1 rounded-full border border-emerald-500/20">
              ✅ Completed
            </span>
          ) : status === "failed" ? (
            <span className="flex items-center gap-1.5 text-xs text-red-400 bg-red-500/10 px-3 py-1 rounded-full border border-red-500/20">
              ❌ Failed
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs text-fg0 bg-surface-overlay px-3 py-1 rounded-full border border-border-strong">
              Idle
            </span>
          )}
        </div>
        <div
          ref={consoleRef}
          className="flex-1 font-mono text-xs text-fg-subtle bg-background p-4 rounded-xl border border-border overflow-y-auto max-h-[420px] flex flex-col gap-1.5"
        >
          {logs.length === 0 ? (
            <div className="text-fg-subtle italic">
              {running ? "Waiting for agent output..." : "Select source and target, then click Run Autopilot."}
            </div>
          ) : (
            logs.map((lg, i) => (
              <div
                key={i}
                className={`flex gap-2 ${lg.level === "error" ? "text-red-400" : lg.level === "warning" ? "text-amber-400" : "text-fg-muted"}`}
              >
                <span className="shrink-0">{STEP_ICONS[lg.step] ?? "•"}</span>
                <span className="text-fg0 shrink-0">[{new Date(lg.created_at).toLocaleTimeString()}]</span>
                <span>{lg.message}</span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Control Panel */}
      <div className="flex flex-col gap-4 rounded-2xl bg-surface border border-border p-6 backdrop-blur-sm justify-between">
        <div>
          <h3 className="font-semibold text-fg-muted mb-2">Autopilot Panel</h3>
          <p className="text-xs text-fg-subtle leading-relaxed mb-4">
            AI autonomously matches schemas, detects PII, and generates migration SQL.
          </p>
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-fg0">Source Database</label>
              <select
                value={sourceId}
                onChange={(e) => setSourceId(e.target.value)}
                className="bg-surface-overlay border border-border-strong text-fg-muted text-sm rounded-lg px-3 py-2"
                disabled={running}
              >
                <option value="">— Select source —</option>
                {connectors.map((c) => (
                  <option key={c.id} value={String(c.id)}>{c.name}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-fg0">Target Database</label>
              <select
                value={targetId}
                onChange={(e) => setTargetId(e.target.value)}
                className="bg-surface-overlay border border-border-strong text-fg-muted text-sm rounded-lg px-3 py-2"
                disabled={running}
              >
                <option value="">— Select target —</option>
                {connectors.filter((c) => String(c.id) !== sourceId).map((c) => (
                  <option key={c.id} value={String(c.id)}>{c.name}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-fg0">Mode</label>
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as "suggest" | "execute")}
                className="bg-surface-overlay border border-border-strong text-fg-muted text-sm rounded-lg px-3 py-2"
                disabled={running}
              >
                <option value="suggest">Suggest Only</option>
                <option value="execute">Execute Pipeline (requires approval)</option>
              </select>
              {mode === "execute" && (
                <p className="text-[11px] text-amber-400/80 leading-snug">
                  Execution writes rows into the target and is irreversible —
                  it will be queued for admin approval, not run immediately.
                </p>
              )}
            </div>
            <div className="p-3 rounded-xl bg-surface-overlay border border-border flex flex-col">
              <span className="text-xs text-fg0">Selected Model</span>
              <span className="text-sm font-semibold text-fg-muted">Llama3 (Ollama)</span>
            </div>
            {sourceName && targetName && (
              <div className="p-3 rounded-xl bg-surface-overlay border border-border flex flex-col">
                <span className="text-xs text-fg0">Target Action</span>
                <span className="text-sm font-semibold text-fg-muted">{sourceName} → {targetName}</span>
              </div>
            )}
          </div>
        </div>

        {error && (
          <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-xs">{error}</div>
        )}

        <div className="flex flex-col gap-2">
          <button
            onClick={handleRun}
            disabled={running || !sourceId || !targetId}
            className="w-full py-2 bg-blue-600 hover:bg-blue-500 rounded-xl text-sm font-semibold text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {running ? "Running..." : mode === "execute" ? "Queue for Approval" : "Run Autopilot"}
          </button>
          {running && (
            <button
              onClick={handleStop}
              className="w-full py-2 bg-surface-overlay hover:bg-surface-overlay rounded-xl text-sm font-semibold text-fg-subtle transition-all border border-transparent hover:border-border-strong"
            >
              Stop
            </button>
          )}
          {runId && !running && (
            <div className="text-xs text-fg-subtle text-center font-mono">run: {runId.slice(0, 8)}…</div>
          )}
        </div>
      </div>
    </div>
  );
}
