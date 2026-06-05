import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles/globals.css";

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
    // eslint-disable-next-line no-console
    console.error("[Prism] Render error:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            padding: 24,
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
            fontSize: 13,
            lineHeight: 1.5,
            color: "#fca5a5",
            background: "#0f172a",
            minHeight: "100vh",
            boxSizing: "border-box",
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 8, color: "#f87171" }}>
            Prism — render error
          </div>
          <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
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
