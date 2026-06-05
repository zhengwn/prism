/**
 * Theme runtime helpers — no React, no Zustand.
 *
 * Used by:
 *   - `lib/theme-init.ts`  →  FOUC bootstrap (runs at top of main.tsx)
 *   - `store/theme.ts`      →  live Zustand store + cycle logic
 *
 * Keep this module side-effect-free and synchronous so it can be required
 * from the FOUC path without dragging in any framework code.
 */

export type Theme = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "prism-theme";

export function readStoredTheme(): Theme {
  if (typeof window === "undefined") return "system";
  const v = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (v === "light" || v === "dark" || v === "system") return v;
  return "system";
}

export function systemPrefersDark(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

/** Resolve a stored Theme to whether `.dark` should be on <html>. */
export function resolveDark(theme: Theme): boolean {
  if (theme === "dark") return true;
  if (theme === "light") return false;
  return systemPrefersDark();
}

/** Apply theme to <html>. Idempotent. */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (resolveDark(theme)) {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
}
