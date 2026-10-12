"use client";

/**
 * ThemeProvider — theme_redesign_tasks #1
 * --------------------------------------------------------------
 * Client-side theme context. The actual `dark`/`light` class is
 * applied to <html> by an inline <script> in app/layout.tsx BEFORE
 * React paints, so the initial state below is just whatever the
 * script already put on the document — first render matches the
 * DOM and there's no hydration warning.
 *
 * No new deps. No SSR cookie. No flash. Persists to localStorage
 * under `dp_theme`. Default = dark (matches previous behaviour).
 */

import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

export type Theme = "dark" | "light";

export interface ThemeContextValue {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggle: () => void;
}

export const ThemeContext = createContext<ThemeContextValue | null>(null);

export const THEME_STORAGE_KEY = "dp_theme";

function readInitialTheme(): Theme {
  // In production the inline <script> in app/layout.tsx has already
  // added the class to <html> before React mounts — that's the source
  // of truth here. We fall back to localStorage for environments
  // (jsdom tests, etc.) where the inline script doesn't run, so the
  // initial render still matches the user's persisted choice.
  if (typeof document !== "undefined") {
    if (document.documentElement.classList.contains("light")) return "light";
    if (document.documentElement.classList.contains("dark")) return "dark";
  }
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // localStorage may be unavailable (private mode); fall through.
  }
  return "dark";
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readInitialTheme);

  // Keep the <html> class and localStorage in sync whenever theme changes.
  // Also defend against another tab changing the storage key.
  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove("dark", "light");
    root.classList.add(theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // localStorage may be unavailable (private mode); ignore.
    }
  }, [theme]);

  // Cross-tab sync — if user opens two tabs and toggles in one,
  // the other picks it up immediately.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== THEME_STORAGE_KEY || !e.newValue) return;
      if (e.newValue === "dark" || e.newValue === "light") {
        setThemeState(e.newValue);
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const setTheme = useCallback((t: Theme) => setThemeState(t), []);

  const toggle = useCallback(
    () => setThemeState((prev) => (prev === "dark" ? "light" : "dark")),
    [],
  );

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme, toggle }),
    [theme, setTheme, toggle],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}
