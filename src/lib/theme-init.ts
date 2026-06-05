/**
 * FOUC-safe theme bootstrap.
 *
 * This module is imported for its side effect at the very top of `main.tsx`,
 * before React is even created. It must run synchronously so the first paint
 * already has the correct `<html class="dark">` state — otherwise the user
 * sees a brief flash of the wrong theme on every reload.
 *
 * Strategy:
 *   1. Read stored theme from localStorage (default = "system").
 *   2. Resolve to dark/light using `prefers-color-scheme` if "system".
 *   3. Add or remove `.dark` on <html>.
 *
 * No React, no JSX, no async — keep it minimal.
 */
import { applyTheme, readStoredTheme, type Theme } from "./theme-runtime";

function init(): void {
  if (typeof document === "undefined") return;
  let theme: Theme;
  try {
    theme = readStoredTheme();
  } catch {
    // localStorage may throw in private mode / SSR fallback
    theme = "system";
  }
  applyTheme(theme);
}

init();
