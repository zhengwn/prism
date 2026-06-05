import { create } from "zustand";
import {
  applyTheme,
  readStoredTheme,
  type Theme,
  THEME_STORAGE_KEY,
} from "@/lib/theme-runtime";

/**
 * Theme slice — independent of the main `usePrismStore` to avoid coupling
 * persistence/UI-toggle side effects to the data store.
 *
 * Three values:
 *   - "light"  → always light
 *   - "dark"   → always dark
 *   - "system" → follow `prefers-color-scheme` media query, live
 *
 * Persistence: `localStorage[THEME_STORAGE_KEY]`.
 * DOM effect:  toggles `<html class="dark">`.
 *
 * The initial DOM class is set by `@/lib/theme-init` (imported for side
 * effects at the very top of `main.tsx`) so the first paint is correct
 * and there is no FOUC.
 *
 * Pure helpers (read, resolve, apply) live in `@/lib/theme-runtime` so
 * the FOUC bootstrap doesn't need to load zustand.
 */

export type { Theme };

interface ThemeState {
  theme: Theme;
  setTheme: (t: Theme) => void;
  cycleTheme: () => void;
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: readStoredTheme(),

  setTheme: (t) => {
    applyTheme(t);
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(THEME_STORAGE_KEY, t);
      } catch {
        // localStorage may throw in private mode — ignore
      }
    }
    set({ theme: t });
  },

  cycleTheme: () => {
    const order: Theme[] = ["light", "dark", "system"];
    const cur = get().theme;
    const next = order[(order.indexOf(cur) + 1) % order.length];
    get().setTheme(next);
  },
}));

// ---------------------------------------------------------------------------
// Live follow for `system` mode
// ---------------------------------------------------------------------------
// Re-apply the dark class whenever the OS-level color scheme changes, but
// only when the user has opted into "system". Attached exactly once at
// module load.

if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const handler = () => {
    if (useThemeStore.getState().theme === "system") {
      applyTheme("system");
    }
  };
  if (typeof mq.addEventListener === "function") {
    mq.addEventListener("change", handler);
  } else if (typeof (mq as MediaQueryList).addListener === "function") {
    // Older Safari
    (mq as MediaQueryList).addListener(handler);
  }
}
