import { useCallback, useEffect, useState } from "react";
import {
  type ResolvedTheme,
  type Theme,
  applyTheme,
  getStoredTheme,
  getSystemTheme,
  resolveTheme,
  setStoredTheme,
} from "@/lib/theme";

/**
 * useTheme — read and change the current theme mode.
 *
 *   const { theme, setTheme, resolvedTheme } = useTheme();
 *
 * - `theme` is the user-chosen mode ("light" | "dark" | "system").
 * - `resolvedTheme` is what's actually applied to the DOM right now
 *   ("light" | "dark") — for "system", it tracks the OS preference and
 *   updates live when the user toggles macOS / Windows dark mode.
 * - `setTheme` persists the choice and reapplies the class.
 *
 * The initial state is read from localStorage on first mount. The very first
 * paint is already correct because index.html runs a synchronous inline
 * script that applies the theme before React loads.
 */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => getStoredTheme() ?? "system");
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() =>
    resolveTheme(getStoredTheme() ?? "system"),
  );

  // Re-apply class + re-resolve on every theme change.
  useEffect(() => {
    const resolved = resolveTheme(theme);
    setResolvedTheme(resolved);
    applyTheme(resolved);
    setStoredTheme(theme);
  }, [theme]);

  // When the user is on "system", follow the OS live. `matchMedia.addEventListener`
  // is the modern API; the older addListener fallback covers very old WebKits
  // (Tauri 2 on older macOS may still ship one).
  useEffect(() => {
    if (theme !== "system") return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      const resolved = getSystemTheme();
      setResolvedTheme(resolved);
      applyTheme(resolved);
    };
    if (mql.addEventListener) {
      mql.addEventListener("change", handler);
      return () => mql.removeEventListener("change", handler);
    }
    mql.addListener(handler);
    return () => mql.removeListener(handler);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
  }, []);

  return { theme, setTheme, resolvedTheme };
}
