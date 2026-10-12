import { beforeEach, describe, expect, it, vi } from "vitest";

const { getMock, postPublicMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postPublicMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { get: getMock, postPublic: postPublicMock },
}));

import { auth } from "../auth";

describe("auth", () => {
  beforeEach(() => {
    localStorage.clear();
    getMock.mockReset();
    postPublicMock.mockReset();
  });

  it("stores the bearer token after a successful login", async () => {
    postPublicMock.mockResolvedValue({
      access_token: "signed-token",
      token_type: "bearer",
      email: "admin@example.com",
      role: "admin",
    });

    await expect(auth.login(" admin@example.com ", "secret")).resolves.toEqual({
      email: "admin@example.com",
      role: "admin",
    });
    expect(postPublicMock).toHaveBeenCalledWith("/api/v1/auth/login", {
      email: "admin@example.com",
      password: "secret",
    });
    expect(auth.token()).toBe("signed-token");
  });

  it("validates the session against the backend", async () => {
    getMock.mockResolvedValue({ id: 1, email: "admin@example.com", role: "admin" });
    await auth.currentUser();
    expect(getMock).toHaveBeenCalledWith("/api/v1/auth/me");
  });

  it("clears authentication and session-expiry state on logout", () => {
    localStorage.setItem("dp_token", "signed-token");
    localStorage.setItem("dp_session_expired_with_pending", "2");
    auth.logout();
    expect(auth.hasSession()).toBe(false);
    expect(localStorage.getItem("dp_session_expired_with_pending")).toBeNull();
  });
});
