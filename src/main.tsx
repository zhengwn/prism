// FOUC-free theme bootstrap is handled by an inline <script> in index.html
// that runs synchronously before this module loads — see the docstring there
// and `src/lib/theme.ts` for the resolver that the inline script mirrors.

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles/globals.css";
// Side-effect import: initializes i18next with the user's chosen language
// (or the OS preference on first launch) and registers the React context.
// Must run before any component calls useTranslation().
import "./i18n";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

/**
 * Top-level ErrorBoundary so any render error shows up as a visible fallback
 * instead of an unmounted black webview. The webview's body bg is dark, so
 * an empty <div id="root"> looks identical to a render crash.
 */
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[Prism] Render error:", error, info);
  }

  render() {
    if (this.state.error) {
      // Use semantic theme tokens so the fallback is readable in both
      // light and dark mode. (Inline color literals would lock the page
      // to dark and look broken in a light theme.)
      return (
        <div className="min-h-screen bg-background p-6 font-mono text-[13px] leading-relaxed text-destructive">
          <div className="mb-2 font-semibold">Prism — render error</div>
          <pre className="m-0 whitespace-pre-wrap">
            {this.state.error.message}
            {"\n\n"}
            {this.state.error.stack}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  // NOTE: StrictMode disabled in v0.1 — its dev double-invoke of effects
  // interacts badly with the Tauri 2 + macOS WebKit + Vite dev pipeline
  // and was producing phantom unmounts. Re-enable once we move to a
  // production-bundle dev workflow.
  <ErrorBoundary>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </ErrorBoundary>,
);
