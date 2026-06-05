// i18next initialization. Imported once from main.tsx; safe to import elsewhere
// for the typed resources export.
//
// Synchronous init on purpose: translation resources are static JSON that Vite
// inlines, so init() resolves before React's first render. No Suspense /
// useSuspense fallback needed.
//
// Detection: localStorage override > OS navigator.language > English.
// The OS detection is done in lib/language.ts so the logic stays testable
// and we don't pull window/navigator into module-init code.
//
// To add a new language:
//   1. Create i18n/<code>.json with the same shape as en.json.
//   2. Extend the Language union and SUPPORTED_LANGUAGES in lib/language.ts.
//   3. Register the resource in the `resources` block below.
//   4. Add the entry in LANGUAGE_LABELS so the Settings picker can show it.

import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";
import zh from "./zh.json";
import {
  detectInitialLanguage,
  setStoredLanguage,
} from "@/lib/language";

const initialLng = detectInitialLanguage();
// Persist the resolved initial value so the next launch skips detection.
setStoredLanguage(initialLng);

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    zh: { translation: zh },
  },
  lng: initialLng,
  fallbackLng: "en",
  interpolation: {
    // React already escapes interpolated values, so disable i18next's
    // escaping to avoid double-escaping HTML entities.
    escapeValue: false,
  },
  // We don't have a real backend; missing keys should be loud (the default
  // behavior is to log a console warning and return the key itself), which
  // is what we want for a v0.1 in-tree dictionary.
  returnEmptyString: false,
  // The `count` plural rule for English is simple (_one / _other). We don't
  // need a CLDR pluralRules polyfill for v0.1, but if Chinese is added with
  // plural-sensitive copy later, configure `compatibilityJSON: "v4"` here.
});

export default i18n;

// Typed access to the resources, so `t('detail.summary')` is autocompleted
// and a typo is a compile error in components that use `useTranslation`.
export type Resources = {
  en: { translation: typeof en };
  zh: { translation: typeof zh };
};
