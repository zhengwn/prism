// Language management for Prism.
//
// Mirrors the shape of src/lib/theme.ts: pure functions for storage and
// detection, the React-side useLanguage hook lives in src/hooks/useLanguage.ts.
//
// We use ISO 639-1 codes ("en" / "zh") for the in-app locale identifier.
// BCP 47 ("zh-CN", "zh-TW") can be added later by extending the Language
// union; the storage key + detection logic is already generic.

export const SUPPORTED_LANGUAGES = ["en", "zh"] as const;
export type Language = (typeof SUPPORTED_LANGUAGES)[number];

export const LANGUAGE_STORAGE_KEY = "prism-language";

function isSupported(value: string | null | undefined): value is Language {
  return value === "en" || value === "zh";
}

/** Safe localStorage read. Returns null if missing, corrupted, or unavailable. */
export function getStoredLanguage(): Language | null {
  try {
    const raw = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return isSupported(raw) ? raw : null;
  } catch {
    return null;
  }
}

export function setStoredLanguage(lang: Language): void {
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, lang);
  } catch {
    // Storage may be disabled (private mode, quota) — silently ignore.
    // The language still applies for this session, it just won't persist.
  }
}

/**
 * Pick the initial language on first launch.
 *   1. If the user picked one before, use that.
 *   2. Otherwise, honor the OS preference if it's a supported language family.
 *   3. Fall back to English.
 */
export function detectInitialLanguage(): Language {
  const stored = getStoredLanguage();
  if (stored) return stored;
  const nav = typeof navigator !== "undefined" ? navigator.language : null;
  if (nav && nav.toLowerCase().startsWith("zh")) return "zh";
  return "en";
}

/** Human-readable label for the language picker. */
export const LANGUAGE_LABELS: Record<Language, string> = {
  en: "English",
  zh: "中文",
};
