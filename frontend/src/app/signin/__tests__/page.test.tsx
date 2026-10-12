import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { replaceMock, loginMock, setTokenMock } = vi.hoisted(() => ({
  replaceMock: vi.fn(),
  loginMock: vi.fn(),
  setTokenMock: vi.fn(),
}));

let currentSearchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: () => currentSearchParams,
}));

vi.mock("@/lib/api", () => ({
  api: { base: "http://test-api" },
  ApiError: class ApiError extends Error {
    constructor(public status: number, message: string) { super(message); }
  },
}));

vi.mock("@/lib/auth", () => ({
  auth: { login: loginMock, setToken: setTokenMock },
}));

vi.mock("@/components/Brand", () => ({ Brand: () => <span>Brand</span> }));
vi.mock("@/lib/theme", () => ({ ThemeToggle: () => <button>Theme</button> }));

describe("LoginPage — Microsoft Entra round trip", () => {
  const ORIGINAL_ENTRA_ENABLED = process.env.NEXT_PUBLIC_ENTRA_ENABLED;

  beforeEach(() => {
    replaceMock.mockReset();
    loginMock.mockReset();
    setTokenMock.mockReset();
    currentSearchParams = new URLSearchParams();
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_ENTRA_ENABLED = ORIGINAL_ENTRA_ENABLED;
    vi.resetModules();
  });

  it("stores the token and redirects to /dashboard when entra_token is present", async () => {
    currentSearchParams = new URLSearchParams({ entra_token: "jwt-abc" });
    vi.resetModules();
    const { default: LoginPage } = await import("../page");
    render(<LoginPage />);

    await waitFor(() => expect(setTokenMock).toHaveBeenCalledWith("jwt-abc"));
    expect(replaceMock).toHaveBeenCalledWith("/dashboard");
  });

  it("shows the no-account message and does not redirect when entra_error=no_account", async () => {
    currentSearchParams = new URLSearchParams({ entra_error: "no_account" });
    vi.resetModules();
    const { default: LoginPage } = await import("../page");
    render(<LoginPage />);

    await screen.findByText(/no dataone account exists for that microsoft identity/i);
    expect(setTokenMock).not.toHaveBeenCalled();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("shows a generic message for other entra_error values", async () => {
    currentSearchParams = new URLSearchParams({ entra_error: "state_mismatch" });
    vi.resetModules();
    const { default: LoginPage } = await import("../page");
    render(<LoginPage />);

    await screen.findByText(/unable to sign in with microsoft/i);
  });

  it("hides the Microsoft button when NEXT_PUBLIC_ENTRA_ENABLED is not set", async () => {
    delete process.env.NEXT_PUBLIC_ENTRA_ENABLED;
    vi.resetModules();
    const { default: LoginPage } = await import("../page");
    render(<LoginPage />);

    expect(screen.queryByText(/sign in with microsoft/i)).not.toBeInTheDocument();
  });

  it("renders the Microsoft button pointing at the backend entra login route when enabled", async () => {
    process.env.NEXT_PUBLIC_ENTRA_ENABLED = "true";
    vi.resetModules();
    const { default: LoginPage } = await import("../page");
    render(<LoginPage />);

    const link = await screen.findByText(/sign in with microsoft/i);
    expect(link.closest("a")).toHaveAttribute(
      "href",
      "http://test-api/api/v1/auth/entra/login",
    );
  });
});
