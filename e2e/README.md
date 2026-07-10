# Prism E2E (Playwright)

Frontend end-to-end tests for Prism, added in v0.2c.

## What this covers

Playwright drives the **real React UI** in Chromium against the Vite dev
server, with the Python sidecar's HTTP API mocked (`mock-sidecar.ts`).
The suite is hermetic — no sidecar process, no database, no LLM key
needed. Specs live in `smoke.spec.ts`:

- inbox loads and renders distilled items
- manual sync fires `POST /api/sync` and surfaces a result toast
- add-source dialog creates an **X** source with a bridge feed URL
- settings shows the sidecar version + the Apply & Restart control

## Running

```bash
# one-time (blocked in the offline dev sandbox; run on your machine):
npm i -D @playwright/test
npx playwright install chromium

# run:
npm run test:e2e         # headless
npm run test:e2e:ui      # Playwright UI mode
```

The config's `webServer` auto-starts `npm run dev` (port 1420) and reuses
an already-running dev server locally.

## Scope caveat — this is NOT full Tauri-shell E2E

Playwright cannot attach to Prism's **native** Tauri window: Tauri renders
through the OS webview (WKWebView on macOS, WebView2 on Windows), which
exposes no CDP endpoint for Playwright. In the browser, `isTauri()` is
`false`, so the app uses its HTTP fallbacks and the keystore/`invoke`
paths (API-key storage, `reveal_llm_key`, `set_llm_config`,
`restart_sidecar`) are **not** exercised here.

True shell-level E2E (real `invoke` commands, the AES-256-GCM keystore,
sidecar spawn/shutdown) needs `tauri-driver` + WebdriverIO, which speaks
the WebDriver protocol Tauri supports. That's tracked as a follow-up in
`docs/ROADMAP.md`. The Playwright layer here is the fast, hermetic UI
regression net; the WDIO layer would be the slower, real-shell net on top.
