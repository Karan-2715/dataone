"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Brand } from "@/components/Brand";
import { api } from "@/lib/api";
import { auth, type AuthUser } from "@/lib/auth";
import { ThemeToggle } from "@/lib/theme";
import { Badge } from "./components/Badge";
import { Gauge } from "./components/Gauge";
import CopilotPanel from "./components/CopilotPanel";
import GlobalSearchPalette from "./components/GlobalSearchPalette";
import NotificationCenter from "./components/NotificationCenter";
import { KpiNotAvailable } from "./components/StateViews";
import { LLMSelectModal } from "./components/LLMSelectModal";

interface NavItem {
  id: string;
  label: string;
  icon: React.ReactNode;
  href: string;
  badge?: string;
}

// Icon components (SVG-based for crisp dark mode rendering)
const Icon = ({ path, className = "w-4 h-4" }: { path: string; className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d={path} />
  </svg>
);

const NAV_ITEMS: NavItem[] = [
  { id: "connectors", label: "Connectors", icon: <Icon path="M13 10V3L4 14h7v7l9-11h-7z" />, href: "/dashboard/connectors" },
  { id: "dashboard", label: "Dashboard", icon: <Icon path="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z M9 22V12h6v10" />, href: "/dashboard" },
  { id: "autopilot", label: "Agentic DBA Copilot", icon: <Icon path="M16 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2 M8 7a4 4 0 100-8 4 4 0 000 8z M20 8v6 M23 11h-6" />, href: "/dashboard/autopilot" },
  { id: "schema-mapper", label: "Schema Mapper", icon: <Icon path="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3" />, href: "/dashboard/schema-mapper" },
  { id: "governance", label: "Autopilot Governance", icon: <Icon path="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />, href: "/dashboard/governance" },
  { id: "askdata", label: "AskData (NL2SQL)", icon: <Icon path="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />, href: "/dashboard/query-workspace" },
  { id: "visualize", label: "Data Visualization", icon: <Icon path="M18 20V10M12 20V4M6 20v-6" />, href: "/dashboard/visualize" },
  { id: "audit", label: "Audit Trail &\nCross-Source Intelligence", icon: <Icon path="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />, href: "/dashboard/audit" },
  { id: "workspace-setup", label: "Workspace Setup", icon: <Icon path="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z" />, href: "/dashboard/workspace-setup" },
];

function activeNavId(pathname: string): string | undefined {
  return NAV_ITEMS
    .filter((i) => pathname === i.href || pathname.startsWith(i.href + "/"))
    .sort((a, b) => b.href.length - a.href.length)[0]?.id;
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [connections, setConnections] = useState<Array<{ environment?: string }>>([]);
  const [governanceScore, setGovernanceScore] = useState<number | null>(null);
  const [authError, setAuthError] = useState("");
  const [collapsed, setCollapsed] = useState(false);

  const validateSession = useCallback(async () => {
    if (!auth.hasSession()) { router.replace("/signin"); return; }
    try {
      const currentUser = await auth.currentUser();
      setUser(currentUser);
      api.get<unknown[]>("/api/v1/connectors/")
        .then((rows) => setConnections(rows as Array<{ environment?: string }>))
        .catch(() => setConnections([]));
      api.get<{ score: number }>("/api/v1/governance/score")
        .then((d) => setGovernanceScore(d.score))
        .catch(() => setGovernanceScore(null));
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Unable to validate your session.");
    }
  }, [router]);

  useEffect(() => {
    const t = window.setTimeout(() => void validateSession(), 0);
    return () => window.clearTimeout(t);
  }, [validateSession]);

  const active = activeNavId(pathname);

  if (!user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#09090b]">
        {authError ? (
          <div className="max-w-sm rounded-2xl bg-white/5 border border-white/10 p-6 text-center">
            <p className="text-sm text-red-400">{authError}</p>
            <button onClick={() => { setAuthError(""); void validateSession(); }}
              className="mt-4 rounded-lg bg-white/20 px-4 py-2 text-sm font-semibold text-white">
              Retry
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-2 border-white/20 border-t-transparent rounded-full animate-spin" />
            <p className="text-sm text-white/40">Validating session…</p>
          </div>
        )}
      </div>
    );
  }

  const initials = user.email.charAt(0).toUpperCase();

  return (
    <div className="flex h-screen w-full bg-[#09090b] font-sans overflow-hidden">
      {/* ── Sidebar ── */}
      {true && (
        <aside
          className={[
            "flex flex-col border-r border-white/[0.06] transition-all duration-300 bg-[#0d0d0d] flex-shrink-0 z-20",
            collapsed ? "w-[80px]" : "w-[260px]",
          ].join(" ")}
        >
        {/* Brand */}
        <div className={["flex items-center h-20", collapsed ? "justify-center px-0" : "px-6 gap-3"].join(" ")}>
          {!collapsed ? (
            <div className="flex items-center gap-3">
              <span className="text-white text-xl font-bold tracking-wide">Veltris</span>
              <div className="w-px h-5 bg-white/20" />
              <span className="text-white text-xl font-bold tracking-wide">DataOne</span>
            </div>
          ) : (
            <span className="text-white text-xl font-bold">D</span>
          )}
        </div>

        {/* Collapse toggle */}
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="flex items-center justify-center h-8 text-white/20 hover:text-white/50 border-b border-white/[0.06] transition-colors"
          aria-label={collapsed ? "Expand" : "Collapse"}
        >
          <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d={collapsed ? "M9 18l6-6-6-6" : "M15 18l-6-6 6-6"} />
          </svg>
        </button>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto py-2 space-y-1 px-4">
          {NAV_ITEMS.map((item) => {
            const isActive = active === item.id;
            return (
              <Link
                key={item.id}
                href={item.href}
                title={collapsed ? item.label : undefined}
                className={[
                  "flex items-center gap-4 px-3 py-2.5 rounded-lg text-[15px] transition-all group relative",
                  collapsed ? "justify-center" : "",
                  isActive
                    ? "bg-white/10 text-white font-medium"
                    : "text-white/60 hover:text-white hover:bg-white/5 font-normal border border-transparent",
                ].join(" ")}
              >
                <span className={["flex-shrink-0 transition-colors", isActive ? "text-white" : "text-white/40 group-hover:text-white"].join(" ")}>
                  {item.icon}
                </span>
                {!collapsed && (
                  <span className="leading-snug whitespace-pre-line">{item.label}</span>
                )}
              </Link>
            );
          })}
        </nav>

        {/* User section */}
        <div className="p-6 pb-8">
          <div className={["flex items-center gap-3", collapsed ? "flex-col justify-center" : ""].join(" ")}>
            <div className="w-10 h-10 rounded-full bg-white/10 text-white flex items-center justify-center text-sm font-semibold flex-shrink-0">
              {initials}
            </div>
            {!collapsed && (
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-white truncate">{user?.email ? user.email.split("@")[0] : "User"}</div>
                <div className="text-xs text-white/40 truncate">Data Engineer</div>
              </div>
            )}
            <button
              onClick={() => { auth.logout(); window.location.href = "/signin"; }}
              className={["flex items-center justify-center text-white/40 hover:text-white hover:bg-white/10 transition-colors", collapsed ? "w-10 h-10 rounded-full" : "w-8 h-8 rounded-lg"].join(" ")}
              title="Log out"
            >
              <Icon path="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </button>
          </div>
          {!collapsed && (
            <div className="mt-6">
              <div className="text-xs text-white/40 mb-1">Workspace</div>
              <div className="flex items-center justify-between text-sm text-white font-medium cursor-pointer">
                Production
                <svg className="w-4 h-4 text-white/50" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M6 9l6 6 6-6" /></svg>
              </div>
            </div>
          )}
          {!collapsed && (
            <div className="mt-8 flex items-center gap-2">
              <div className="w-2 h-2 bg-emerald-500 rounded-full animate-pulse flex-shrink-0" />
              <span className="text-xs text-white/50 flex-1">Session Active</span>
              <span className="text-xs text-white/50">2h 14m</span>
            </div>
          )}
        </div>
      </aside>
      )}

      {/* ── Main column ── */}
      <main className="flex-1 flex flex-col overflow-hidden relative">
        {/* We removed the generic layout header so pages can render their own edge-to-edge top bars */}

        {/* Page content */}
        <div className="flex-1 overflow-y-auto bg-[#09090b]">
          {children}
        </div>
      </main>

      {/* LLM Selection Modal for first-time login */}
      {user && !user.llm_model && (
        <LLMSelectModal onComplete={(model) => setUser({ ...user, llm_model: model })} />
      )}
    </div>
  );
}
