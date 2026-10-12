// @vitest-environment jsdom
/**
 * ThemeProvider tests — theme_redesign_tasks #1
 * --------------------------------------------------------------
 * Covers:
 *   - default theme is "dark" when no localStorage entry exists
 *   - default theme is "light" when localStorage says "light"
 *   - default theme is "dark" when localStorage says "dark"
 *   - setTheme("light") flips <html> class and writes localStorage
 *   - toggle() alternates dark ↔ light
 *   - useTheme throws when used outside a provider (wiring safety net)
 */

import { act, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  THEME_STORAGE_KEY,
  ThemeProvider,
  useTheme,
} from "@/lib/theme";

function readHtmlClass(): string[] {
  return Array.from(document.documentElement.classList);
}

function readStorage(): string | null {
  return localStorage.getItem(THEME_STORAGE_KEY);
}

describe("ThemeProvider", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
  });

  afterEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
  });

  it('defaults to "dark" when localStorage has no entry', () => {
    render(
      <ThemeProvider>
        <span>child</span>
      </ThemeProvider>,
    );
    expect(readHtmlClass()).toContain("dark");
    expect(readHtmlClass()).not.toContain("light");
    // The provider writes the active theme to localStorage on mount
    // so a reload preserves the choice.
    expect(readStorage()).toBe("dark");
  });

  it('honors localStorage "dark"', () => {
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    render(
      <ThemeProvider>
        <span>child</span>
      </ThemeProvider>,
    );
    expect(readHtmlClass()).toContain("dark");
  });

  it('honors localStorage "light"', () => {
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    render(
      <ThemeProvider>
        <span>child</span>
      </ThemeProvider>,
    );
    expect(readHtmlClass()).toContain("light");
  });

  it("setTheme flips the class and persists", () => {
    const { result } = renderHook(() => useTheme(), {
      wrapper: ThemeProvider,
    });
    expect(result.current.theme).toBe("dark");

    act(() => result.current.setTheme("light"));
    expect(result.current.theme).toBe("light");
    expect(readHtmlClass()).toContain("light");
    expect(readHtmlClass()).not.toContain("dark");
    expect(readStorage()).toBe("light");

    act(() => result.current.setTheme("dark"));
    expect(result.current.theme).toBe("dark");
    expect(readHtmlClass()).toContain("dark");
    expect(readHtmlClass()).not.toContain("light");
    expect(readStorage()).toBe("dark");
  });

  it("toggle() alternates dark ↔ light", () => {
    const { result } = renderHook(() => useTheme(), {
      wrapper: ThemeProvider,
    });
    expect(result.current.theme).toBe("dark");

    act(() => result.current.toggle());
    expect(result.current.theme).toBe("light");
    expect(readHtmlClass()).toContain("light");

    act(() => result.current.toggle());
    expect(result.current.theme).toBe("dark");
    expect(readHtmlClass()).toContain("dark");
  });

  it("only ever has one of dark|light on <html>", () => {
    const { result } = renderHook(() => useTheme(), {
      wrapper: ThemeProvider,
    });
    act(() => result.current.setTheme("light"));
    act(() => result.current.setTheme("dark"));
    act(() => result.current.toggle());
    const classes = readHtmlClass();
    const themeClasses = classes.filter((c) => c === "dark" || c === "light");
    expect(themeClasses).toHaveLength(1);
  });

  it("useTheme throws outside a ThemeProvider", () => {
    // Suppress React's error boundary noise for the expected throw.
    const orig = console.error;
    console.error = () => {};
    try {
      expect(() => renderHook(() => useTheme())).toThrow(/ThemeProvider/);
    } finally {
      console.error = orig;
    }
  });
});
