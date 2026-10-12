"use client";

import { useContext } from "react";
import { ThemeContext, type ThemeContextValue } from "./ThemeProvider";

/**
 * useTheme — accessor for the ThemeContext.
 * Throws if called outside a ThemeProvider (development safety net —
 * surfaces wiring mistakes instead of returning undefined).
 */
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error(
      "useTheme must be used inside <ThemeProvider>. Check app/layout.tsx.",
    );
  }
  return ctx;
}
