// Theme management for Prism.
//
// Three modes: "light" / "dark" / "system". Only the first two are stored —
// "system" defers to the OS preference via `prefers-color-scheme` and updates
// live when the user toggles macOS / Windows dark mode.
//
// The actual <html class="dark"> mutation happens in two places:
//   1. A tiny inline script in index.html, which runs synchronously before
//      React mounts, so the first paint already shows the correct theme.
//      (Otherwise light-mode users see a black flash on every launch.)
//   2. applyTheme() below, which the React side uses after mount for any
//      subsequent changes (user picks a new mode, system theme flips, etc.).

export type Theme = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "prism-theme";

/** Safe localStorage read — guards against private-mode / disabled storage. */
export function getStoredTheme(): Theme | null {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
    return null;
  } catch {
    return null;
  }
}

export function setStoredTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Storage may be disabled (private mode, quota, etc.) — silently ignore.
    // The theme still applies for this session, it just won't persist.
  }
}

/** Read the OS preference. Returns "light" if the API is unavailable. */
export function getSystemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || !window.matchMedia) return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/** Collapse a Theme (which may be "system") into the concrete class to apply. */
export function resolveTheme(theme: Theme): ResolvedTheme {
  return theme === "system" ? getSystemTheme() : theme;
}

/**
 * Apply the concrete theme to <html>. Toggles the .dark class and the
 * color-scheme meta value so native widgets (scrollbars, form controls,
 * <input type="date">) follow along.
 */
export function applyTheme(resolved: ResolvedTheme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
}
