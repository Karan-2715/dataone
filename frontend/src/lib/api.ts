const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8011";

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem("dp_token");
  } catch (e) {
    return null;
  }
}

function authHeaders(includeContentType = true): Record<string, string> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (includeContentType) headers["Content-Type"] = "application/json";
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
    headers["X-DataOne-Auth"] = `Bearer ${token}`;
  }
  return headers;
}

// FastAPI HTTPException `detail` is sometimes a plain string, but several
// endpoints (schema-mapper publish/validate/edge grammar errors) intentionally
// send a structured object, e.g. {kind, message, issues: [...]}, so the UI can
// eventually branch on it. Passing that object straight into `Error(message)`
// silently stringifies to the literal text "[object Object]" (JS's Error
// constructor coerces via ToString) — every toast/banner reading `.message`
// showed that instead of the real reason, which is exactly what made a failed
// Schema Mapper publish look "stuck" with no visible explanation. Always
// resolve to a human-readable string here instead.
function errorMessageFromDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.length > 0) return detail;
  if (Array.isArray(detail)) {
    // FastAPI's own request-validation errors: [{msg, loc}, ...].
    const msgs = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : null,
      )
      .filter((m): m is string => !!m);
    if (msgs.length > 0) return msgs.join("; ");
  } else if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    if (typeof d.message === "string" && d.message.length > 0) return d.message;
    if (Array.isArray(d.issues)) {
      const msgs = d.issues
        .map((iss) =>
          iss && typeof iss === "object" && "message" in iss
            ? String((iss as { message: unknown }).message)
            : null,
        )
        .filter((m): m is string => !!m);
      if (msgs.length > 0) return msgs.join("; ");
    }
    try {
      return JSON.stringify(detail);
    } catch {
      return fallback;
    }
  }
  return fallback;
}

/** Parse a non-2xx Response's JSON body and throw the resulting ApiError. */
async function throwApiError(res: Response): Promise<never> {
  const body = await res.json().catch(() => ({}));
  throw new ApiError(res.status, errorMessageFromDetail(body?.detail, res.statusText));
}

function handle401() {
  if (typeof window !== "undefined") {
    // Allow higher-level code (e.g. useMapping) to run a best-effort flush
    // and surface a user-visible warning BEFORE the token is cleared and
    // the browser navigates. If no handler is registered, fall back to the
    // original silent-redirect behavior so non-schema-mapper pages are
    // unaffected (mapper_tasks/05_session_timeout_autosave_loss.md).
    // Each handler is isolated in its own try/catch: one throwing must not
    // stop the others from running, and must not abort the token clear +
    // redirect below — that would wedge the app on an expired token,
    // re-401ing every call (review_schema_mapper_round2 #12).
    for (const fn of unauthorizedHandlers) {
      try {
        fn();
      } catch (err) {
        console.error("unauthorized handler failed", err);
      }
    }
    try {
      localStorage.removeItem("dp_token");
    } catch (e) {
      // Ignore
    }
    window.location.href = "/signin";
  }
}

// Pluggable 401 callbacks so feature code (e.g. useMapping) can warn the
// user and attempt a best-effort flush before the token-clear + hard
// navigation happens. A Set (not a single slot) so multiple mounted
// features can register concurrently without clobbering each other
// (dashboard_tasks/bugs #03 — a single nullable slot let a second
// registration silently overwrite, and a stale unmount's `null` clear
// could wipe out a still-mounted feature's handler).
const unauthorizedHandlers = new Set<() => void>();

/** Register a callback; returns an unregister function to call on cleanup. */
export function addUnauthorizedHandler(fn: () => void): () => void {
  unauthorizedHandlers.add(fn);
  return () => unauthorizedHandlers.delete(fn);
}

export const api = {
  base: API_BASE,

  async postPublic<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) await throwApiError(res);
    return res.json() as Promise<T>;
  },

  async get<T>(path: string, options?: { signal?: AbortSignal }): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: authHeaders(false),
      signal: options?.signal,
    });
    if (res.status === 401) { handle401(); throw new ApiError(401, "Unauthorized"); }
    if (!res.ok) await throwApiError(res);
    return res.json() as Promise<T>;
  },

  async post<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: authHeaders(true),
      body: JSON.stringify(body),
    });
    if (res.status === 401) { handle401(); throw new ApiError(401, "Unauthorized"); }
    if (!res.ok) await throwApiError(res);
    return res.json() as Promise<T>;
  },

  async postForm<T>(path: string, body: FormData): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: authHeaders(false),
      body: body,
    });
    if (res.status === 401) { handle401(); throw new ApiError(401, "Unauthorized"); }
    if (!res.ok) await throwApiError(res);
    return res.json() as Promise<T>;
  },

  async put<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "PUT",
      headers: authHeaders(true),
      body: JSON.stringify(body),
    });
    if (res.status === 401) { handle401(); throw new ApiError(401, "Unauthorized"); }
    if (!res.ok) await throwApiError(res);
    return res.json() as Promise<T>;
  },

  async patch<T>(path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "PATCH",
      headers: authHeaders(body !== undefined),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) { handle401(); throw new ApiError(401, "Unauthorized"); }
    if (!res.ok) await throwApiError(res);
    return res.json() as Promise<T>;
  },

  async download(path: string): Promise<{ blob: Blob; filename: string }> {
    const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders(false) });
    if (res.status === 401) { handle401(); throw new ApiError(401, "Unauthorized"); }
    if (!res.ok) await throwApiError(res);
    const disposition = res.headers.get("Content-Disposition") ?? "";
    const match = /filename="?([^"]+)"?/.exec(disposition);
    const blob = await res.blob();
    return { blob, filename: match?.[1] ?? "download" };
  },

  async downloadPost(path: string, body: unknown): Promise<{ blob: Blob; filename: string }> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: authHeaders(true),
      body: JSON.stringify(body),
    });
    if (res.status === 401) { handle401(); throw new ApiError(401, "Unauthorized"); }
    if (!res.ok) await throwApiError(res);
    const disposition = res.headers.get("Content-Disposition") ?? "";
    const match = /filename="?([^"]+)"?/.exec(disposition);
    const blob = await res.blob();
    return { blob, filename: match?.[1] ?? "download" };
  },

  async delete(path: string): Promise<void> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "DELETE",
      headers: authHeaders(false),
    });
    if (res.status === 401) { handle401(); throw new ApiError(401, "Unauthorized"); }
    if (!res.ok) await throwApiError(res);
  },

  /** Like delete(), but returns the parsed JSON body — for endpoints (e.g.
   * "delete with dependency warning") that respond 200 with a body instead
   * of 204, whose caller needs to branch on that body. */
  async deleteWithResponse<T>(path: string): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "DELETE",
      headers: authHeaders(false),
    });
    if (res.status === 401) { handle401(); throw new ApiError(401, "Unauthorized"); }
    if (!res.ok) await throwApiError(res);
    return res.json() as Promise<T>;
  },

  // ── Databricks Ingestion typed helpers ──
  async triggerIngestion(sourceConnectionId: number, targetConnectionId?: number) {
    return this.post<{
      id: number; databricks_run_id: number | null; databricks_job_id: number;
      source_type: string; status: string; databricks_run_url: string | null;
    }>("/api/v1/databricks/ingest/trigger", {
      source_connection_id: sourceConnectionId,
      target_connection_id: targetConnectionId,
    });
  },

  async getIngestionRunStatus(ingestionRunId: number) {
    return this.get<{
      id: number; databricks_run_id: number | null; status: string;
      rows_ingested: number | null; error_message: string | null;
      completed_at: string | null; databricks_run_url: string | null;
    }>(`/api/v1/databricks/ingest/runs/${ingestionRunId}`);
  },

  async listIngestionPipelines() {
    return this.get<{ pipelines: Array<{ id: number; source_type: string; job_id: number; job_name: string }> }>(
      "/api/v1/databricks/ingest/pipelines"
    );
  },

  async triggerGenieAI(question: string, spaceId?: string) {
    return this.post<{ answer: string | null; conversation_id?: string; error?: string }>(
      "/api/v1/databricks/ingest/genie",
      { question, space_id: spaceId }
    );
  },
};

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
