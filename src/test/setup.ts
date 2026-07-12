import "@testing-library/jest-dom/vitest";
import { afterEach, beforeAll, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

// jsdom doesn't implement matchMedia. useTheme() calls it when the theme is
// "system" (the default), so any component that mounts the theme hook (e.g.
// TopBar) would throw without this shim. Report a stable "light" preference;
// tests that care about theme assert on the applied class, not the query.
if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

// jsdom stubs scrollIntoView as undefined. The command palette calls it to
// keep the highlighted row visible while arrowing; no-op it in tests.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = vi.fn();
}

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
