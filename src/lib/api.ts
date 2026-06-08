/**
 * Prism API client.
 *
 * Two transport layers:
 *   - HTTP: hits the Python sidecar at `SIDECAR_BASE` (loopback only).
 *   - Tauri invoke: for things the webview can't/shouldn't do via HTTP.
 *     Currently the only consumer is the OS-keychain API key store, which
 *     is implemented as Tauri commands in `src-tauri/`.
 *
 * In dev (pure Vite, no Tauri shell) the invoke calls will fail with
 * `__TAURI_INTERNALS__ is undefined`. The callers (Settings page) treat
 * that as "no key configured" rather than crashing — the UI shows a clean
 * "not configured" state and the user can still configure it once the
 * Tauri build is run.
 */

import { invoke } from "@tauri-apps/api/core";
import type { KnowledgeItem, PrismHealth, Source, SyncJob, SyncResult } from "@/types";

const SIDECAR_BASE = import.meta.env.VITE_PRISM_SIDECAR_URL ?? "http://127.0.0.1:8765";

class PrismAPIError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "PrismAPIError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${SIDECAR_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new PrismAPIError(res.status, text || res.statusText);
  }
  return res.json() as Promise<T>;
}

/**
 * Detect whether we're running inside a Tauri webview. In pure Vite dev
 * (or a regular browser) the global is undefined and `invoke()` would
 * throw a noisy "window.__TAURI_INTERNALS__ is undefined" — guard the
 * keychain calls with this.
 */
function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export const api = {
  // ----- Health -----
  health: () => request<PrismHealth>("/health"),

  // ----- Sources -----
  listSources: () => request<Source[]>("/api/sources"),
  getSource: (id: string) => request<Source>(`/api/sources/${id}`),
  createSource: (data: Omit<Source, "id" | "itemCount" | "lastSyncedAt">) =>
    request<Source>("/api/sources", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  patchSource: (
    id: string,
    data: Partial<Pick<Source, "name" | "url" | "enabled">>,
  ) =>
    request<Source>(`/api/sources/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteSource: (id: string) =>
    request<{ ok: true }>(`/api/sources/${id}`, { method: "DELETE" }),

  // ----- Items -----
  listItems: (params?: { sourceId?: string; status?: string; q?: string }) => {
    const search = new URLSearchParams();
    if (params?.sourceId) search.set("sourceId", params.sourceId);
    if (params?.status) search.set("status", params.status);
    if (params?.q) search.set("q", params.q);
    const qs = search.toString();
    return request<KnowledgeItem[]>(`/api/items${qs ? `?${qs}` : ""}`);
  },
  getItem: (id: string) => request<KnowledgeItem>(`/api/items/${id}`),

  // ----- Sync -----
  /**
   * POST /api/sync — runs the full pipeline. v0.2a is synchronous: the
   * call returns only after every enabled source has been fetched and
   * distilled (or attempted). The toast uses the `itemsNew` /
   * `itemsDistilled` counters to summarise what happened.
   */
  syncAll: () => request<SyncResult>("/api/sync", { method: "POST" }),
  /** POST /api/sync/{sourceId} — sync a single source. */
  syncOne: (sourceId: string) =>
    request<SyncResult>(`/api/sync/${sourceId}`, { method: "POST" }),
  /** GET /api/sync/{jobId} — poll status of a sync job. */
  getSyncStatus: (jobId: string) => request<SyncJob>(`/api/sync/${jobId}`),
  /** GET /api/sync/history?limit=N — list recent sync runs. */
  getSyncHistory: (limit?: number) =>
    request<SyncJob[]>(`/api/sync/history${limit ? `?limit=${limit}` : ""}`),

  // ----- Distill -----
  /**
   * GET /api/distill/pending-count — how many items are waiting to be
   * distilled. Used by the Settings "重蒸馏所有 pending" button to show
   * the user how much work is queued.
   */
  getPendingDistillCount: () =>
    request<{ pending: number }>("/api/distill/pending-count"),
  /**
   * POST /api/distill/redistill — re-run distillation on every item with
   * `distilled_at IS NULL`. Use cases:
   *   - user just configured an API key for the first time
   *   - user's key expired / ran out and they want a clean re-run
   * The response's `keyInvalid` field tells the UI to stop retrying.
   */
  redistill: () =>
    request<{
      startedPending: number;
      distilled: number;
      failed: number;
      keyInvalid: boolean;
      error?: string;
      sampleFailures: string[];
    }>("/api/distill/redistill", { method: "POST" }),

  // ----- API key (Tauri-only) -----
  /**
   * Whether an LLM API key is stored in the OS keychain. Returns
   * `configured: false` in non-Tauri contexts (pure Vite dev) — there
   * the call is a no-op so the UI degrades gracefully.
   */
  getApiKeyStatus: async (): Promise<{ configured: boolean }> => {
    if (!isTauri()) return { configured: false };
    return invoke<{ configured: boolean }>("get_api_key_status");
  },
  setApiKey: async (key: string): Promise<{ ok: true }> => {
    if (!isTauri()) throw new Error("API key storage is only available inside the Tauri app");
    return invoke<{ ok: true }>("set_api_key", { key });
  },
  clearApiKey: async (): Promise<{ ok: true }> => {
    if (!isTauri()) return { ok: true };
    return invoke<{ ok: true }>("clear_api_key");
  },
};

export { PrismAPIError, SIDECAR_BASE };
