import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // `e2e/*.spec.ts` are Playwright specs — they import `@playwright/test`
    // and drive a real browser. Vitest's default include glob (`**/*.spec.ts`)
    // would otherwise collect them and fail at import time.
    exclude: [...configDefaults.exclude, "e2e/**"],
    // The InboxPage test exercises real timers (the sync success toast
    // auto-dismisses after 2.5s). Using fake timers would require us to
    // call vi.advanceTimersByTime explicitly, which adds noise. Real
    // timers with a 5s test timeout is plenty.
    testTimeout: 5_000,
    css: false,
  },
});
