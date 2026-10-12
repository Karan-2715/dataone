"use client";
/**
 * useMapping — central state + actions for one open mapping.
 *
 * Owns: mapping, edges, suggestions, selectedEdgeId, dirty/saving/lastSaved,
 *       validation, role, exportVersionId.
 *
 * All persistence happens through the backend; this hook is purely a
 * thin orchestrator with optimistic updates + rollback on error.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { addUnauthorizedHandler, api, ApiError } from "@/lib/api";
import type {
  AISuggestion,
  EdgeTransformationUpdate,
  ExportArtifact,
  FieldMapping,
  Mapping,
  Paginated,
  PublishResponse,
  Role,
  SourceRef,
  SuggestionAcceptRequest,
  TargetRef,
  TransformationPayload,
  ValidationResponse,
} from "../lib/types";

const AUTOSAVE_INTERVAL_MS = 30_000;

// The suggestion panel wants "every pending suggestion for this mapping" in
// one shot, not a browsable page -- so it requests the server's max page
// size (see le=200 on GET /mappings/{id}/suggestions) rather than paging.
// A mapping with more than 200 unmapped target columns at once (20% of the
// TRD's 1,000-column ceiling) would need real pagination here too; tracked
// as a known limit rather than built out speculatively.
const SUGGESTIONS_PAGE_LIMIT = 200;

async function fetchAllSuggestions(mappingId: number): Promise<AISuggestion[]> {
  const page = await api.get<Paginated<AISuggestion>>(
    `/api/v1/mappings/${mappingId}/suggestions?limit=${SUGGESTIONS_PAGE_LIMIT}`,
  );
  return page.items;
}

export interface UseMappingResult {
  mapping: Mapping | null;
  edges: FieldMapping[];
  suggestions: AISuggestion[];
  pendingSuggestions: AISuggestion[];
  decidedSuggestions: AISuggestion[];
  selectedEdgeId: number | null;
  dirty: boolean;
  saving: boolean;
  lastSavedAt: string | null;
  error: string | null;
  toast: { kind: "info" | "error" | "success"; message: string } | null;
  validation: ValidationResponse | null;
  exportVersionId: number | null;
  exportArtifact: ExportArtifact | null;
  role: Role | null;
  currentUserEmail: string | null;

  load(mappingId: number): Promise<void>;
  close(): void;
  create(input: { name: string; source_id: number; target_id: number }): Promise<Mapping>;
  refresh(): Promise<void>;
  addEdge(input: {
    target: TargetRef;
    sources: SourceRef[];
    transformation: TransformationPayload;
    origin?: FieldMapping["origin"];
  }): Promise<FieldMapping | null>;
  removeEdge(edgeId: number): Promise<void>;
  rename(name: string): Promise<void>;
  updateTransformation(edgeId: number, transformation: TransformationPayload): Promise<void>;
  selectEdge(edgeId: number | null): void;
  requestSuggestions(): Promise<void>;
  acceptSuggestion(suggestionId: number, transformation?: TransformationPayload): Promise<void>;
  rejectSuggestion(suggestionId: number): Promise<void>;
  validate(): Promise<ValidationResponse | null>;
  clearValidation(): void;
  publish(): Promise<PublishResponse | null>;
  revise(): Promise<void>;
  rollbackToVersion(versionId: number): Promise<void>;
  loadExport(versionId?: number): Promise<ExportArtifact | null>;
  clearExport(): void;
  clearToast(): void;
  /** True while an AI-suggestion generation task is in flight for this mapping. */
  generatingSuggestions: boolean;
  /** Lightweight info toast for interaction guidance (e.g. Canvas hints). */
  hint(message: string): void;
}

export function useMapping(): UseMappingResult {
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [suggestions, setSuggestions] = useState<AISuggestion[]>([]);
  const [selectedEdgeId, setSelectedEdgeId] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<UseMappingResult["toast"]>(null);
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [exportVersionId, setExportVersionId] = useState<number | null>(null);
  const [exportArtifact, setExportArtifact] = useState<ExportArtifact | null>(null);
  const [role, setRole] = useState<Role | null>(null);
  const [currentUserEmail, setCurrentUserEmail] = useState<string | null>(null);
  const [generatingSuggestions, setGeneratingSuggestions] = useState(false);

  const mappingIdRef = useRef<number | null>(null);
  // In-flight guard for requestSuggestions, mirroring flushingRef below: a
  // ref (not just the `generatingSuggestions` state) so a fast double-click
  // is blocked synchronously even before React re-renders the disabled
  // button. Without this, AI suggestion generation — LLM-backed and slow —
  // had no "already running" signal at all (the panel's `loading` prop was
  // hardcoded false), so re-clicking fanned out multiple concurrent
  // suggest_mappings_task runs that could each create duplicate suggestions
  // for the same target column (uiux bug report: "not idempotent").
  const suggestingRef = useRef(false);
  const dirtyQueueRef = useRef<Array<() => Promise<void>>>([]);
  const flushTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // In-flight guard for flushDirty. MUST be a ref, not the `saving` state:
  // the interval/visibilitychange handlers are registered in a mount-once
  // effect and forever call the first render's flushDirty, whose closed-over
  // `saving` is permanently false — a state-based guard never blocks those
  // call sites, so two drains could overlap and double-shift the queue
  // (running one op twice and dropping the next without executing it —
  // review_schema_mapper_round2 #1). A ref reads current from any closure.
  const flushingRef = useRef(false);
  // Set by the 401 handler so the beforeunload prompt stands down during the
  // forced redirect to /login (review_schema_mapper_round2 #5).
  const sessionExpiredRef = useRef(false);

  const showToast = useCallback(
    (kind: "info" | "error" | "success", message: string) => {
      setToast({ kind, message });
      setTimeout(() => setToast(null), 5000);
    },
    [],
  );

  // Fetch the current user's role on mount.
  useEffect(() => {
    let cancelled = false;
    api
      .get<{ role: Role; email: string }>("/api/v1/auth/me")
      .then((me) => {
        if (!cancelled) {
          setRole(me.role);
          setCurrentUserEmail(me.email);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRole(null);
          setCurrentUserEmail(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 30 s autosave flush + visibilitychange + beforeunload + 401 handler.
  // All three are mounted/unmounted together because they share the same
  // flushDirty / dirtyQueueRef lifecycle (mapper_tasks #5).
  useEffect(() => {
    flushTimerRef.current = setInterval(() => {
      if (dirtyQueueRef.current.length > 0) void flushDirty();
    }, AUTOSAVE_INTERVAL_MS);
    const onVis = () => {
      if (document.visibilityState === "hidden") void flushDirty();
    };
    document.addEventListener("visibilitychange", onVis);

    // beforeunload: warn the user if there are unsaved edits queued. The
    // browser shows its own native confirmation dialog. We can't reliably
    // flush async PUTs here (the browser kills in-flight requests); the
    // best we can do is ask the user to cancel the navigation.
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      // Don't prompt on the 401 forced redirect: the queue is non-empty by
      // definition in that path (that's what triggered the warning flag),
      // but staying on the page can't save anything — the token is already
      // gone. Without this, the native "Leave site?" dialog interrupts the
      // logout, and choosing "Stay" strands the user on a dead session that
      // re-prompts on the next API call (review_schema_mapper_round2 #5).
      if (sessionExpiredRef.current) return;
      if (dirtyQueueRef.current.length > 0) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);

    // 401: best-effort warning before api.ts clears the token and navigates
    // to /login. We deliberately do NOT attempt a flush here — the token is
    // already expired, so a PUT would just 401 again. The toast is a
    // secondary signal only: handle401's hard navigation can unload the
    // page before this React state update ever paints, so the durable
    // signal is the localStorage flag, which the login page reads and
    // displays as a banner (frontend/src/app/login/page.tsx) — that's what
    // actually survives the redirect and closes the silent-loss NFR gap
    // mapper_tasks #5 targeted.
    const onUnauthorized = () => {
      // Stand the beforeunload prompt down before handle401 hard-navigates —
      // see onBeforeUnload above (review_schema_mapper_round2 #5).
      sessionExpiredRef.current = true;
      const pending = dirtyQueueRef.current.length;
      if (pending > 0) {
        try {
          localStorage.setItem(
            "dp_session_expired_with_pending",
            String(pending),
          );
        } catch {
          // localStorage may be unavailable (private mode etc.) — best-effort.
        }
        showToast(
          "error",
          `Session expired with ${pending} unsaved change${pending === 1 ? "" : "s"}. ` +
            `Log back in and re-apply your last edit.`,
        );
      }
    };
    const removeUnauthorizedHandler = addUnauthorizedHandler(onUnauthorized);

    return () => {
      if (flushTimerRef.current) clearInterval(flushTimerRef.current);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("beforeunload", onBeforeUnload);
      removeUnauthorizedHandler();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Drain the queue one op at a time, removing each only after it succeeds.
  // A failure partway through leaves the failing op (and anything queued
  // after it) in dirtyQueueRef so the next flush retries them and the
  // beforeunload/401 "unsaved changes" warnings keep seeing a truthful
  // pending count — splicing the whole queue out up front (the prior
  // behavior) discarded failed ops silently, defeating those warnings.
  const flushDirty = useCallback(async () => {
    if (flushingRef.current || dirtyQueueRef.current.length === 0) return;
    flushingRef.current = true;
    setSaving(true);
    try {
      while (dirtyQueueRef.current.length > 0) {
        await dirtyQueueRef.current[0]();
        dirtyQueueRef.current.shift();
      }
      setLastSavedAt(new Date().toISOString());
      setDirty(false);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "Autosave failed.";
      setError(message);
      showToast("error", message);
    } finally {
      flushingRef.current = false;
      setSaving(false);
    }
  }, [showToast]);

  const enqueue = useCallback((op: () => Promise<void>) => {
    dirtyQueueRef.current.push(op);
    setDirty(true);
  }, []);

  // ── Data fetch ───────────────────────────────────────────────

  const load = useCallback(
    async (mappingId: number) => {
      mappingIdRef.current = mappingId;
      setMapping(null);
      setSuggestions([]);
      setSelectedEdgeId(null);
      setValidation(null);
      setExportArtifact(null);
      setError(null);
      // A different mapping's in-flight AI-suggestion request (if any) no
      // longer concerns this one — reset the guard so the newly-loaded
      // mapping's "AI Suggest" button starts enabled rather than inheriting
      // a stuck "Generating…" state from whatever was previously open.
      suggestingRef.current = false;
      setGeneratingSuggestions(false);
      try {
        const [m, s] = await Promise.all([
          api.get<Mapping>(`/api/v1/mappings/${mappingId}`),
          fetchAllSuggestions(mappingId).catch(() => [] as AISuggestion[]),
        ]);
        setMapping(m);
        setSuggestions(s);
      } catch (err) {
        const message =
          err instanceof ApiError ? err.message : "Failed to load mapping.";
        setError(message);
        showToast("error", message);
      }
    },
    [showToast],
  );

  const close = useCallback(() => {
    mappingIdRef.current = null;
    setMapping(null);
    setSuggestions([]);
    setSelectedEdgeId(null);
    setValidation(null);
    setExportArtifact(null);
  }, []);

  const refresh = useCallback(async () => {
    if (mappingIdRef.current === null) return;
    await load(mappingIdRef.current);
  }, [load]);

  // ── CRUD ──────────────────────────────────────────────────────

  const create = useCallback(
    async (input: { name: string; source_id: number; target_id: number }) => {
      const created = await api.post<Mapping>("/api/v1/mappings/", input);
      showToast("success", `Created draft "${created.name}".`);
      return created;
    },
    [showToast],
  );

  const addEdge: UseMappingResult["addEdge"] = useCallback(
    async (input) => {
      if (!mapping) return null;
      // Optimistic insert.
      const tempId = -Date.now();
      const optimistic: FieldMapping = {
        id: tempId,
        mapping_id: mapping.id,
        target: input.target,
        sources: input.sources,
        transformation: input.transformation,
        origin: input.origin ?? "manual",
        ai_confidence: null,
        audit: {},
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setMapping({ ...mapping, edges: [...mapping.edges, optimistic] });
      try {
        const real = await api.post<FieldMapping>(
          `/api/v1/mappings/${mapping.id}/edges`,
          {
            target: input.target,
            sources: input.sources,
            transformation: input.transformation,
            origin: input.origin ?? "manual",
          },
        );
        // Replace optimistic with real.
        setMapping((prev) =>
          prev
            ? { ...prev, edges: prev.edges.map((e) => (e.id === tempId ? real : e)) }
            : prev,
        );
        setLastSavedAt(new Date().toISOString());
        return real;
      } catch (err) {
        setMapping((prev) =>
          prev
            ? { ...prev, edges: prev.edges.filter((e) => e.id !== tempId) }
            : prev,
        );
        const message =
          err instanceof ApiError ? err.message : "Failed to add edge.";
        showToast("error", message);
        throw err;
      }
    },
    [mapping, showToast],
  );

  const removeEdge: UseMappingResult["removeEdge"] = useCallback(
    async (edgeId) => {
      if (!mapping) return;
      const snapshot = mapping.edges;
      setMapping({ ...mapping, edges: mapping.edges.filter((e) => e.id !== edgeId) });
      if (selectedEdgeId === edgeId) setSelectedEdgeId(null);
      try {
        await api.delete(`/api/v1/mappings/${mapping.id}/edges/${edgeId}`);
        setLastSavedAt(new Date().toISOString());
      } catch (err) {
        // Functional update: roll back only the `edges` field onto whatever
        // mapping state is current, rather than the closure's `mapping`
        // snapshot, which would clobber any change (e.g. a concurrent
        // rename) that landed while this delete was in flight.
        setMapping((prev) => (prev ? { ...prev, edges: snapshot } : prev));
        const message =
          err instanceof ApiError ? err.message : "Failed to remove edge.";
        showToast("error", message);
        throw err;
      }
    },
    [mapping, selectedEdgeId, showToast],
  );

  // Rename the mapping (TRD FR8 implied; mapper_tasks #6). Mirrors the
  // removeEdge pattern: snapshot the current name, optimistically update
  // local state, PUT to the server, roll back + toast on failure.
  // Skipped from the autosave queue — a rename is a deliberate single
  // action, not a stream of small autosaved edits.
  const rename: UseMappingResult["rename"] = useCallback(
    async (name: string) => {
      if (!mapping) return;
      const trimmed = name.trim();
      if (!trimmed || trimmed === mapping.name) return;
      const snapshot = mapping.name;
      setMapping((prev) => (prev ? { ...prev, name: trimmed } : prev));
      try {
        await api.put<Mapping>(`/api/v1/mappings/${mapping.id}`, { name: trimmed });
        setLastSavedAt(new Date().toISOString());
        showToast("success", "Mapping renamed.");
      } catch (err) {
        // Functional update, same reasoning as removeEdge above: an
        // in-flight addEdge/removeEdge that resolves while this PUT is
        // pending must not be silently discarded by the rollback.
        setMapping((prev) => (prev ? { ...prev, name: snapshot } : prev));
        const message =
          err instanceof ApiError ? err.message : "Failed to rename mapping.";
        showToast("error", message);
        throw err;
      }
    },
    [mapping, showToast],
  );

  const updateTransformation = useCallback(
    async (edgeId: number, transformation: TransformationPayload) => {
      if (!mapping) return;
      const body: EdgeTransformationUpdate = { transformation };
      enqueue(async () => {
        await api.put<FieldMapping>(
          `/api/v1/mappings/${mapping.id}/edges/${edgeId}/transformation`,
          body,
        );
      });
      // Apply optimistically; the queued PUT will reconcile if it fails.
      setMapping({
        ...mapping,
        edges: mapping.edges.map((e) =>
          e.id === edgeId ? { ...e, transformation } : e,
        ),
      });
    },
    [enqueue, mapping],
  );

  const selectEdge = useCallback((edgeId: number | null) => {
    setSelectedEdgeId(edgeId);
  }, []);

  // ── AI suggestions ───────────────────────────────────────────

  const requestSuggestions = useCallback(async () => {
    if (!mapping) return;
    // Re-entrancy guard: a click while a previous request/poll is still
    // running must be a no-op, not a second task enqueue. The button is
    // disabled while `generatingSuggestions` is true, but the ref catches
    // the race between a fast double-click and that re-render.
    if (suggestingRef.current) return;
    const requestedMappingId = mapping.id;
    suggestingRef.current = true;
    setGeneratingSuggestions(true);
    // Only apply this request's outcome if the user is still (or again) on
    // the mapping that requested it — `load()` already reset the guard on
    // any mapping switch, so a stale poll resolving later must not clobber
    // a *different*, currently-relevant in-flight request for another
    // mapping (or the currently-displayed suggestion list).
    const stillRelevant = () => mappingIdRef.current === requestedMappingId;
    const stopGenerating = () => {
      suggestingRef.current = false;
      setGeneratingSuggestions(false);
    };
    try {
      const { task_id } = await api.post<{ task_id: string }>(
        `/api/v1/mappings/${requestedMappingId}/suggestions`,
        {},
      );
      showToast("info", "Generating AI suggestions…");
      // Poll the task until SUCCESS/FAILURE. Only a terminal status clears
      // the in-flight guard — a transient poll-fetch error retries instead
      // of silently re-enabling the button while generation may still be
      // running server-side.
      const poll = async () => {
        try {
          const status = await api.get<{
            status: string;
            result?: { suggestions_created?: number } | null;
          }>(`/api/v1/tasks/${task_id}`);
          if (status.status === "SUCCESS") {
            if (stillRelevant()) {
              const fresh = await fetchAllSuggestions(requestedMappingId);
              setSuggestions(fresh);
              showToast(
                "success",
                `Generated ${status.result?.suggestions_created ?? fresh.length} suggestion(s).`,
              );
              stopGenerating();
            }
            return;
          }
          if (status.status === "FAILURE") {
            if (stillRelevant()) {
              showToast("error", "AI suggestion task failed.");
              stopGenerating();
            }
            return;
          }
          setTimeout(poll, 2000);
        } catch {
          setTimeout(poll, 4000);
        }
      };
      setTimeout(poll, 1500);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "Failed to enqueue AI task.";
      showToast("error", message);
      if (stillRelevant()) stopGenerating();
    }
  }, [mapping, showToast]);

  const acceptSuggestion = useCallback(
    async (suggestionId: number, transformation?: TransformationPayload) => {
      if (!mapping) return;
      const body: SuggestionAcceptRequest = {
        transformation: transformation ?? { kind: "direct" },
      };
      try {
        await api.post<FieldMapping>(
          `/api/v1/mappings/${mapping.id}/suggestions/${suggestionId}/accept`,
          body,
        );
        // Refresh both edges and suggestions.
        const [m, s] = await Promise.all([
          api.get<Mapping>(`/api/v1/mappings/${mapping.id}`),
          fetchAllSuggestions(mapping.id),
        ]);
        setMapping(m);
        setSuggestions(s);
        showToast("success", "Suggestion accepted.");
      } catch (err) {
        const message =
          err instanceof ApiError ? err.message : "Failed to accept suggestion.";
        showToast("error", message);
      }
    },
    [mapping, showToast],
  );

  const rejectSuggestion = useCallback(
    async (suggestionId: number) => {
      if (!mapping) return;
      try {
        await api.post(
          `/api/v1/mappings/${mapping.id}/suggestions/${suggestionId}/reject`,
          {},
        );
        const s = await fetchAllSuggestions(mapping.id);
        setSuggestions(s);
        showToast("info", "Suggestion rejected.");
      } catch (err) {
        const message =
          err instanceof ApiError ? err.message : "Failed to reject suggestion.";
        showToast("error", message);
      }
    },
    [mapping, showToast],
  );

  // ── Validate / Publish / Export ───────────────────────────────

  // Shared by validate() (which toasts the outcome) and publish()'s failure
  // path (which refreshes silently — see below) so there is exactly one
  // place that calls POST .../validate and applies the response.
  const runValidation = useCallback(async (mappingId: number) => {
    const v = await api.post<ValidationResponse>(
      `/api/v1/mappings/${mappingId}/validate`,
      {},
    );
    setValidation(v);
    return v;
  }, []);

  const validate = useCallback(async () => {
    if (!mapping) return null;
    try {
      const v = await runValidation(mapping.id);
      if (v.blocking_count === 0 && v.warning_count === 0) {
        showToast("success", "Validation passed — no issues.");
      } else if (v.blocking_count === 0) {
        showToast("info", `Validation passed with ${v.warning_count} warning(s).`);
      } else {
        showToast(
          "error",
          `Validation found ${v.blocking_count} blocking issue(s).`,
        );
      }
      return v;
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "Validation failed.";
      showToast("error", message);
      return null;
    }
  }, [mapping, runValidation, showToast]);

  const clearValidation = useCallback(() => setValidation(null), []);

  const publish = useCallback(async () => {
    if (!mapping) return null;
    try {
      const v = await api.post<PublishResponse>(
        `/api/v1/mappings/${mapping.id}/publish`,
        {},
      );
      showToast("success", `Published v${v.version_number}.`);
      await refresh();
      return v;
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "Publish failed.";
      showToast("error", message);
      // The Publish dialog shows blocking/warning counts from the *last*
      // Validate click, which can be stale (0 blocking) if edits happened
      // since — the backend re-validates on every publish attempt
      // regardless, so a blocking-validation 422 here means that stale
      // count just disagreed with the server. Refresh it silently (no
      // second toast) so the dialog reflects reality on the very next
      // render instead of looking permanently stuck on "safe to publish."
      if (err instanceof ApiError && err.status === 422) {
        try {
          await runValidation(mapping.id);
        } catch {
          // Best-effort — the publish failure itself is already surfaced.
        }
      }
      throw err;
    }
  }, [mapping, refresh, runValidation, showToast]);

  // E13-6 / uiux "createDraftVersion" — reopen a published mapping for
  // editing. Shares one endpoint with rollbackToVersion below; "Revise"
  // is just "roll back to the version you're already on".
  const rollbackToVersion = useCallback(
    async (versionId: number) => {
      if (!mapping) return;
      const wasCurrent = versionId === mapping.current_version_id;
      try {
        await api.post<Mapping>(
          `/api/v1/mappings/${mapping.id}/versions/${versionId}/rollback`,
          {},
        );
        // Refetch (not just adopt the response) so stale validation results
        // and superseded-suggestion state reset consistently with the new
        // draft, the same way publish()'s own refresh does.
        await refresh();
        showToast(
          "success",
          wasCurrent
            ? "Reopened for editing."
            : `Rolled back to v${versionId} and reopened for editing.`,
        );
      } catch (err) {
        const message =
          err instanceof ApiError ? err.message : "Failed to reopen mapping for editing.";
        showToast("error", message);
        throw err;
      }
    },
    [mapping, refresh, showToast],
  );

  const revise = useCallback(async () => {
    if (!mapping || mapping.current_version_id == null) return;
    await rollbackToVersion(mapping.current_version_id);
  }, [mapping, rollbackToVersion]);

  const loadExport = useCallback(
    async (versionId?: number) => {
      if (!mapping) return null;
      try {
        const qs = versionId !== undefined ? `?version_id=${versionId}` : "";
        const a = await api.get<ExportArtifact>(
          `/api/v1/mappings/${mapping.id}/export${qs}`,
        );
        setExportArtifact(a);
        setExportVersionId(versionId ?? null);
        return a;
      } catch (err) {
        const message =
          err instanceof ApiError ? err.message : "Export failed.";
        showToast("error", message);
        return null;
      }
    },
    [mapping, showToast],
  );

  const clearExport = useCallback(() => {
    setExportArtifact(null);
    setExportVersionId(null);
  }, []);

  const clearToast = useCallback(() => setToast(null), []);

  // Thin alias over the existing toast mechanism for lightweight interaction
  // guidance (e.g. Canvas telling the user to stage a source column before
  // a target click can do anything) — reuses the same visible/aria-live
  // surface instead of introducing a second notification UI.
  const hint = useCallback(
    (message: string) => showToast("info", message),
    [showToast],
  );

  // Derived.
  const edges = mapping?.edges ?? [];
  const pendingSuggestions = suggestions.filter((s) => s.status === "pending");
  const decidedSuggestions = suggestions.filter((s) => s.status !== "pending");

  return {
    mapping,
    edges,
    suggestions,
    pendingSuggestions,
    decidedSuggestions,
    selectedEdgeId,
    dirty,
    saving,
    lastSavedAt,
    error,
    toast,
    validation,
    exportVersionId,
    exportArtifact,
    role,
    currentUserEmail,
    generatingSuggestions,
    load,
    close,
    create,
    refresh,
    addEdge,
    removeEdge,
    rename,
    updateTransformation,
    selectEdge,
    requestSuggestions,
    acceptSuggestion,
    rejectSuggestion,
    validate,
    clearValidation,
    publish,
    revise,
    rollbackToVersion,
    loadExport,
    clearExport,
    clearToast,
    hint,
  };
}
