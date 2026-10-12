import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { addUnauthorizedHandler, api } from "../api";

function fakeResponse(body: unknown, status = 200) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as Response;
}

describe("addUnauthorizedHandler", () => {
  const originalLocation = window.location;

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse({ detail: "nope" }, 401)));
    // jsdom throws "not implemented" on real navigation; stub location as a
    // plain writable object so handle401's redirect is a harmless no-op.
    // defineProperty (not assignment) sidesteps window.location's setter
    // being typed as `string & Location` in this lib.dom version.
    Object.defineProperty(window, "location", {
      value: { href: "" },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      value: originalLocation,
      writable: true,
      configurable: true,
    });
    vi.unstubAllGlobals();
  });

  it("runs every registered handler on a 401, not just the first", async () => {
    const first = vi.fn();
    const second = vi.fn();
    addUnauthorizedHandler(first);
    addUnauthorizedHandler(second);

    await expect(api.get("/whatever")).rejects.toThrow();

    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("does not let a throwing handler block the others", async () => {
    const boom = vi.fn(() => {
      throw new Error("handler blew up");
    });
    const fine = vi.fn();
    addUnauthorizedHandler(boom);
    addUnauthorizedHandler(fine);

    await expect(api.get("/whatever")).rejects.toThrow();

    expect(boom).toHaveBeenCalledTimes(1);
    expect(fine).toHaveBeenCalledTimes(1);
  });

  it("unregister removes only its own handler (bugs #03 — no cross-clobbering)", async () => {
    const stillMounted = vi.fn();
    const unmounting = vi.fn();
    addUnauthorizedHandler(stillMounted);
    const removeUnmounting = addUnauthorizedHandler(unmounting);

    removeUnmounting();
    await expect(api.get("/whatever")).rejects.toThrow();

    expect(stillMounted).toHaveBeenCalledTimes(1);
    expect(unmounting).not.toHaveBeenCalled();
  });
});

describe("api.get", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards an AbortSignal to fetch when provided", async () => {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(
      async () => fakeResponse({ ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await api.get("/things", { signal: controller.signal });

    const [, init] = fetchMock.mock.calls[0];
    expect(init).toMatchObject({ signal: controller.signal });
  });

  it("still works with no options (signal is undefined, not required)", async () => {
    const fetchMock = vi.fn(async () => fakeResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.get<{ ok: boolean }>("/things");

    expect(result).toEqual({ ok: true });
  });
});

describe("api.postPublic", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns a login 401 without invoking protected-session navigation", async () => {
    const fetchMock = vi.fn(async () => fakeResponse({ detail: "Invalid credentials" }, 401));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.postPublic("/api/v1/auth/login", {})).rejects.toMatchObject({
      status: 401,
      message: "Invalid credentials",
    });
  });
});

describe("ApiError message extraction from a structured HTTPException detail", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the detail string as-is when it already is one", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse({ detail: "edge not found" }, 404)));
    await expect(api.get("/x")).rejects.toMatchObject({ message: "edge not found" });
  });

  it("extracts .message from a structured object detail instead of stringifying it", async () => {
    // Real shape from mapping_service.py's grammar_error / _assert_draft 409s.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        fakeResponse(
          { detail: { kind: "grammar_error", message: "concat requires 2 source parts", location: "concat.parts" } },
          422,
        ),
      ),
    );
    await expect(api.post("/x", {})).rejects.toMatchObject({
      status: 422,
      message: "concat requires 2 source parts",
    });
  });

  it("joins per-issue messages for a publish validation_blocking detail (the schema-mapper publish bug)", async () => {
    // Real shape from MappingService.publish()'s 422 when blocking_count > 0
    // — before this fix, ApiError's message became the literal string
    // "[object Object]" and the Publish dialog just sat there with no
    // visible reason (uiux bug report #1).
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        fakeResponse(
          {
            detail: {
              kind: "validation_blocking",
              blocking_count: 2,
              issues: [
                { edge_id: 1, verdict: "blocking", message: "target is NOT NULL but source is nullable" },
                { edge_id: 2, verdict: "blocking", message: "unsupported cast TEXT -> JSON" },
              ],
            },
          },
          422,
        ),
      ),
    );
    await expect(api.post("/x", {})).rejects.toMatchObject({
      status: 422,
      message: "target is NOT NULL but source is nullable; unsupported cast TEXT -> JSON",
    });
  });

  it("joins FastAPI's own request-validation error array", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        fakeResponse({ detail: [{ msg: "field required", loc: ["body", "name"] }] }, 422),
      ),
    );
    await expect(api.post("/x", {})).rejects.toMatchObject({
      status: 422,
      message: "field required",
    });
  });

  it("falls back to JSON.stringify for an unrecognized object shape, never '[object Object]'", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse({ detail: { foo: "bar" } }, 500)));
    await expect(api.get("/x")).rejects.toMatchObject({
      status: 500,
      message: JSON.stringify({ foo: "bar" }),
    });
  });

  it("falls back to statusText when the error body has no detail at all", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ status: 503, ok: false, statusText: "Service Unavailable", json: async () => ({}) }) as Response),
    );
    await expect(api.get("/x")).rejects.toMatchObject({
      status: 503,
      message: "Service Unavailable",
    });
  });
});
