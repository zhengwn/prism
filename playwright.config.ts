import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E config — Prism frontend (v0.2c).
 *
 * Scope & caveat
 * --------------
 * Playwright drives the **web frontend** (the Vite dev server) in a real
 * Chromium instance. It does NOT attach to the native Tauri webview —
 * Tauri uses the OS webview (WKWebView / WebView2), which has no CDP
 * endpoint for Playwright to connect to. True Tauri-shell E2E (real
 * `invoke` commands, the AES keystore, sidecar spawn) needs
 * `tauri-driver` + WebdriverIO and is tracked as a separate follow-up in
 * docs/ROADMAP.md.
 *
 * What these specs DO cover: the full React UI against the sidecar HTTP
 * contract. In the browser `isTauri()` is false, so the app already
 * falls back to HTTP for everything except keystore writes — which means
 * the whole inbox / sources / sync / settings surface is exercisable by
 * mocking the sidecar's HTTP endpoints (see `e2e/mock-sidecar.ts`). The
 * suite is hermetic: no Python sidecar and no API key required.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:1420",
    trace: "on-first-retry",
    // The app picks its initial language from `navigator.language`
    // (see lib/language.ts::detectInitialLanguage), which decides whether
    // items render `titleEn` or `titleZh`. Pin the locale so the suite does
    // not depend on the host machine's locale. Specs that need the Chinese
    // UI override this with `test.use({ locale: "zh-CN" })`.
    locale: "en-US",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Boot the Vite dev server for the tests. We reuse an already-running
  // server locally (fast iteration) but always start a fresh one in CI.
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:1420",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
