import { api } from "@/lib/api";

const TOKEN_KEY = "dp_token";

export interface AuthUser {
  id?: number;
  email: string;
  role: string;
  llm_model?: string;
}

interface LoginResponse extends AuthUser {
  access_token: string;
  token_type: string;
}

function storage(): Storage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

export const auth = {
  token(): string | null {
    return storage()?.getItem(TOKEN_KEY) ?? null;
  },

  hasSession(): boolean {
    return Boolean(this.token());
  },

  async login(email: string, password: string): Promise<AuthUser> {
    const result = await api.postPublic<LoginResponse>("/api/v1/auth/login", {
      email: email.trim(),
      password,
    });
    storage()?.setItem(TOKEN_KEY, result.access_token);
    return { email: result.email, role: result.role, llm_model: result.llm_model };
  },

  /** Store a token obtained outside the password flow (e.g. Entra redirect). */
  setToken(token: string): void {
    storage()?.setItem(TOKEN_KEY, token);
  },

  async currentUser(): Promise<AuthUser> {
    return api.get<AuthUser>("/api/v1/auth/me");
  },

  logout(): void {
    storage()?.removeItem(TOKEN_KEY);
    storage()?.removeItem("dp_session_expired_with_pending");
  },
};
