"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { auth } from "@/lib/auth";

const ENTRA_ENABLED = process.env.NEXT_PUBLIC_ENTRA_ENABLED === "true";
const DATABRICKS_MODE = process.env.NEXT_PUBLIC_DATABRICKS_MODE === "true";

const ENTRA_ERROR_MESSAGES: Record<string, string> = {
  no_account: "No DataOne account exists for that Microsoft identity. Contact your administrator.",
};

const DATABRICKS_ERROR_MESSAGES: Record<string, string> = {
  no_account: "No DataOne account exists for your Databricks user. Contact your administrator.",
  no_permissions: "You don't have the required Unity Catalog permissions.",
  oauth_not_configured: "Databricks OAuth is not configured. Please sign in with email and password below.",
  oauth_failed: "Databricks sign-in failed. Please try again or use email and password below.",
};

function RedirectListener({
  onError,
  onShowEmailForm,
}: {
  onError: (msg: string) => void;
  onShowEmailForm: () => void;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  useEffect(() => {
    const token = searchParams.get("entra_token") || searchParams.get("token");
    const entraError = searchParams.get("entra_error");
    const databricksError = searchParams.get("error");
    if (token) {
      auth.setToken(token);
      router.replace("/dashboard/connectors");
    } else if (entraError) {
      onError(ENTRA_ERROR_MESSAGES[entraError] ?? "Unable to sign in with Microsoft.");
    } else if (databricksError) {
      onError(DATABRICKS_ERROR_MESSAGES[databricksError] ?? "Unable to sign in with Databricks.");
      onShowEmailForm();
    }
  }, [searchParams, router, onError, onShowEmailForm]);
  return null;
}

function RotatingHero() {
  const [index, setIndex] = useState(0);

  const slides = [
    {
      title: "AI Schema Mapping",
      subtitle: "Precision Mapping",
      desc: "Automatically analyze and map source schemas to target Databricks Delta Lake tables using our intelligent schema engine.",
      icon: (
        <svg className="w-24 h-24 text-white/20 mb-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3" />
        </svg>
      )
    },
    {
      title: "Conversational Query",
      subtitle: "NL2SQL Genie",
      desc: "Ask questions in plain English and instantly get insights back. No SQL required to query your data in Databricks.",
      icon: (
        <svg className="w-24 h-24 text-white/20 mb-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3" />
        </svg>
      )
    },
    {
      title: "Ingestion Pipelines",
      subtitle: "Unified Orchestration",
      desc: "Trigger, monitor, and manage end-to-end Databricks ingestion pipelines directly from a single unified control plane.",
      icon: (
        <svg className="w-24 h-24 text-white/20 mb-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
        </svg>
      )
    }
  ];

  useEffect(() => {
    const timer = setInterval(() => {
      setIndex((i) => (i + 1) % slides.length);
    }, 4000);
    return () => clearInterval(timer);
  }, [slides.length]);

  return (
    <div className="relative z-10 flex flex-col gap-6 max-w-xl transition-all duration-500 min-h-[300px]">
      <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/10 border border-white/20 text-white text-xs font-semibold w-fit mb-4">
        <span className="w-1.5 h-1.5 bg-white rounded-full animate-pulse" />
        {slides[index].subtitle}
      </div>
      {slides[index].icon}
      <h1 className="text-4xl xl:text-5xl font-bold leading-tight text-white transition-opacity duration-500">
        <span className="block bg-gradient-to-r from-white via-gray-300 to-gray-500 bg-clip-text text-transparent">
          {slides[index].title}
        </span>
      </h1>
      <p className="text-white/60 leading-relaxed text-lg transition-opacity duration-500">
        {slides[index].desc}
      </p>
      
      {/* Slide Indicators */}
      <div className="flex items-center gap-2 mt-4">
        {slides.map((_, i) => (
          <div key={i} className={`h-1 rounded-full transition-all duration-500 ${i === index ? "w-8 bg-white" : "w-2 bg-white/20"}`} />
        ))}
      </div>
    </div>
  );
}

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionExpiredPending, setSessionExpiredPending] = useState<number | null>(null);
  const [showEmailForm, setShowEmailForm] = useState(!DATABRICKS_MODE && !ENTRA_ENABLED);
  const router = useRouter();

  useEffect(() => {
    try {
      const raw = localStorage.getItem("dp_session_expired_with_pending");
      if (raw) {
        const count = Number(raw);
        if (Number.isFinite(count) && count > 0) setSessionExpiredPending(count);
        localStorage.removeItem("dp_session_expired_with_pending");
      }
    } catch { /* localStorage unavailable */ }
  }, []);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await auth.login(email, password);
      router.replace("/dashboard/connectors");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to connect to the authentication service.",
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen bg-[#09090b] font-sans text-white overflow-hidden">
      <Suspense fallback={null}>
        <RedirectListener onError={setError} onShowEmailForm={() => setShowEmailForm(true)} />
      </Suspense>

      {/* ── Left hero panel ── */}
      <div className="hidden lg:flex lg:w-[55%] relative flex-col justify-between p-10 overflow-hidden">
        {/* Gradient background */}
        <div className="absolute inset-0 bg-[#0c0c0e]" />
        
        {/* Grid pattern overlay */}
        <div
          className="absolute inset-0 opacity-[0.02]"
          style={{
            backgroundImage: `linear-gradient(rgba(255,255,255,0.8) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.8) 1px, transparent 1px)`,
            backgroundSize: "40px 40px",
          }}
        />

        {/* Brand */}
        <div className="relative z-10">
          <Link href="/signin" className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-white to-gray-300 flex items-center justify-center shadow-lg shadow-white/10">
              <span className="text-black text-lg font-bold">D</span>
            </div>
            <div>
              <span className="text-xs text-white/50 tracking-widest uppercase font-medium">Veltris</span>
              <div className="text-white font-bold text-lg leading-none">DataOne</div>
            </div>
          </Link>
        </div>

        {/* Hero copy */}
        <RotatingHero />

        <div className="relative z-10 w-full max-w-xl">
          {/* Stats */}
          <div className="grid grid-cols-3 gap-4 mt-4 pt-4 border-t border-white/10">
            {[
              { value: "99.9%", label: "Uptime SLA" },
              { value: "10+", label: "Source connectors" },
              { value: "<1s", label: "Pipeline trigger" },
            ].map((s) => (
              <div key={s.label}>
                <div className="text-2xl font-bold text-white">{s.value}</div>
                <div className="text-xs text-white/50 mt-0.5">{s.label}</div>
              </div>
            ))}
          </div>
        </div>

        <p className="relative z-10 text-xs text-white/30">© 2026 Veltris / DataOne. All rights reserved.</p>
      </div>

      {/* ── Right sign-in panel ── */}
      <div className="flex-1 flex flex-col items-center justify-center px-6 py-12 relative bg-[#09090b]">

        <div className="w-full max-w-sm flex flex-col gap-6 relative z-10">
          {/* Mobile brand */}
          <div className="lg:hidden flex items-center gap-3 mb-2">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-white to-gray-300 flex items-center justify-center">
              <span className="text-black font-bold">D</span>
            </div>
            <div>
              <div className="text-[10px] text-white/50 tracking-widest uppercase">Veltris</div>
              <div className="text-white font-bold">DataOne</div>
            </div>
          </div>

          {/* Heading */}
          <div>
            <h2 className="text-2xl font-bold text-white">Welcome back</h2>
            <p className="text-sm text-white/50 mt-1">Sign in to your DataOne workspace</p>
          </div>

          {/* Session expired banner */}
          {sessionExpiredPending !== null && (
            <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-400 text-xs flex items-start gap-2">
              <svg className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
              <span>Your session expired with {sessionExpiredPending} unsaved change{sessionExpiredPending === 1 ? "" : "s"}. Log back in and re-apply.</span>
            </div>
          )}

          {/* Error banner */}
          {error && (
            <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-xs flex items-start gap-2">
              <svg className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
              <span>{error}</span>
            </div>
          )}

          {/* Databricks sign-in (primary) */}
          <div className="flex flex-col gap-3">
            <a
              id="databricks-signin-btn"
              href={`${api.base}/api/v1/auth/databricks/login`}
              className="w-full py-3.5 text-sm font-semibold text-white bg-white/5 border border-white/10 rounded-xl hover:bg-white/10 backdrop-blur-md active:scale-[0.98] transition-all flex items-center justify-center gap-3"
            >
              <svg role="img" viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5 text-[#FF3621]">
                <path d="M12.012 0L.925 5.253v13.528l11.087 5.219 11.087-5.219V5.253zm0 2.22l8.816 4.148-8.816 4.148-8.816-4.148zm0 9.878l4.408-2.074 4.408 2.074-8.816 4.148-8.816-4.148 4.408-2.074zm0 5.485l4.408-2.074 4.408 2.074-8.816 4.148-8.816-4.148 4.408-2.074z"/>
              </svg>
              Sign in with Databricks
            </a>

            {ENTRA_ENABLED && (
              <a
                href={`${api.base}/api/v1/auth/entra/login`}
                className="w-full py-3 text-sm font-semibold text-white/90 bg-white/5 border border-white/10 rounded-xl hover:bg-white/10 transition-all flex items-center justify-center gap-2"
              >
                <span><svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4h6v6H4zm10 0h6v6h-6zM4 14h6v6H4zm10 0h6v6h-6z"/></svg></span> Sign in with Microsoft
              </a>
            )}
          </div>

          {/* Divider */}
          <div className="flex items-center gap-3">
            <span className="flex-1 h-px bg-white/10" />
            <button
              type="button"
              onClick={() => setShowEmailForm((v) => !v)}
              className="text-xs text-white/40 hover:text-white/60 transition-colors flex items-center gap-1"
            >
              {showEmailForm ? "hide" : "continue with email"}
            </button>
            <span className="flex-1 h-px bg-white/10" />
          </div>

          {/* Email form */}
          {showEmailForm && (
            <form onSubmit={handleLogin} className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-white/50">Email Address</label>
                <input
                  id="email-input"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@company.com"
                  required
                  className="px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white text-sm placeholder:text-white/20 focus:outline-none focus:border-white/40 transition-colors"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-white/50">Password</label>
                <input
                  id="password-input"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  className="px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white text-sm placeholder:text-white/20 focus:outline-none focus:border-white/40 transition-colors"
                />
              </div>
              <button
                id="email-signin-btn"
                type="submit"
                disabled={loading}
                className="w-full py-3 text-sm font-semibold bg-white text-black hover:bg-gray-200 disabled:opacity-50 rounded-xl transition-all shadow-md shadow-white/10"
              >
                {loading ? "Signing in…" : "Sign In"}
              </button>
            </form>
          )}

          <p className="text-xs text-white/30 text-center">
            Access is managed by your DataOne administrator.
          </p>
        </div>
      </div>
    </div>
  );
}
