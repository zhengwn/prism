import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Tauri expects a fixed port and binds the dev server to localhost
const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // Tauri specific dev server settings
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || "127.0.0.1",
    // Disable HMR for Tauri dev — the Vite HMR client + macOS WebKit has a
    // race condition where the webview starts before the bundle is ready
    // and HMR never re-mounts React. Disabling HMR forces Vite to serve the
    // full bundle on first load, which works reliably.
    hmr: false,
    watch: {
      // Tell vite to ignore watching `src-tauri` so it doesn't crash on Rust changes
      ignored: ["**/src-tauri/**"],
    },
  },
  // Env variables prefixed with VITE_ are exposed to the client
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    // Tauri uses Chromium on Windows, WebKit on macOS and Linux
    target: process.env.TAURI_ENV_PLATFORM === "windows" ? "chrome105" : "safari13",
    minify: !process.env.TAURI_ENV_DEBUG ? "esbuild" : false,
    sourcemap: !!process.env.TAURI_ENV_DEBUG,
  },
});
