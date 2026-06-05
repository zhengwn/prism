import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { type Language, setStoredLanguage } from "@/lib/language";

/**
 * useLanguage — read and change the interface language.
 *
 *   const { t, language, setLanguage } = useLanguage();
 *
 * Mirrors the shape of useTheme: t() comes from react-i18next, language is
 * the active locale, and setLanguage persists the choice and re-renders
 * the tree (i18next is reactive via its context).
 */
export function useLanguage() {
  const { t, i18n } = useTranslation();

  // i18next reports the active language as a BCP 47-ish string ("en", "zh",
  // or "zh-CN" if we ever extend). Narrow to our Language union for callers
  // that switch on it (e.g. the Settings picker).
  const language = (i18n.resolvedLanguage ?? i18n.language ?? "en") as Language;

  const setLanguage = useCallback((next: Language) => {
    void i18n.changeLanguage(next);
    setStoredLanguage(next);
  }, [i18n]);

  return { t, language, setLanguage };
}
