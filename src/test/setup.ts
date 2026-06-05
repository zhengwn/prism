import "@testing-library/jest-dom/vitest";
import { afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

// Initialize i18next ONCE for all tests so react-i18next's useTranslation
// hook has an instance to talk to. Without this the components log
// `NO_I18NEXT_INSTANCE` warnings — harmless, but noisy. The empty
// resources are fine: the components under test just see empty
// strings, and the assertions target attributes / testids rather than
// the translated copy.
beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    fallbackLng: "en",
    resources: { en: { translation: {} }, zh: { translation: {} } },
    interpolation: { escapeValue: false },
    returnEmptyString: false,
  });
});

// RTL recommends cleaning up the DOM between tests so the next render
// doesn't see leftover nodes.
afterEach(() => {
  cleanup();
});
