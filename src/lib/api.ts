/**
 * Prism API client.
 *
 * Two transport layers:
 *   - HTTP: hits the Python sidecar at `SIDECAR_BASE` (loopback only).
 *   - Tauri invoke: for things the webview can't/shouldn't do via HTTP.
 *     Currently the only consumer is the OS-keychain-backed LLM config
 *     store, which is implemented as Tauri commands in `src-tauri/`.
 *
 * In dev (pure Vite, no Tauri shell) the invoke calls will fail with
 * `__TAURI_INTERNALS__ is undefined`. The callers (Settings page) treat
 * that as a soft failure rather than crashing — the UI degrades to a
 * "HTTP-only" mode where the sidecar's in-memory provider state is
 * updated but the keychain slot is left alone.
 */

import { invoke } from "@tauri-apps/api/core";
import type {
  DistillProgress,
  KnowledgeItem,
  LlmConfig,
  LlmConfigUpdate,
  PrismHealth,
  ProviderSchema,
  Source,
  SyncLogEntry,
  SyncResult,
} from "@/types";

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
  /**
   * Create a source. The base shape is `Omit<Source, ...server-set fields>`
   * plus an optional `bvid` and an open-ended `config` bag — the sidecar
   * stores `bvid` / `mid` inside `Source.config_json`, but the front-end
   * surfaces a top-level `bvid` for read ergonomics.
   *
   * v0.2c (Bilibili): `kind: "bilibili"` is accepted by the sidecar as a
   * valid source kind; the server routes it to `BilibiliFetcher`. The
   * front-end only needs to pass the kind + a URL (BV id or mid page) +
   * optional bvid and the sidecar handles the rest.
   */
  createSource: (
    data: Omit<Source, "id" | "itemCount" | "lastSyncedAt"> & {
      config?: Record<string, unknown>;
    },
  ) =>
    request<Source>("/api/sources", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  patchSource: (
    id: string,
    data: Partial<Pick<Source, "name" | "url" | "enabled" | "bvid">>,
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
  getSyncStatus: (jobId: string) => request<SyncResult>(`/api/sync/${jobId}`),
  /**
   * POST /api/sync/{jobId}/cancel — ask the sidecar to stop a
   * running sync at the next source boundary. The endpoint is
   * best-effort: it sets a flag, the pipeline observes it
   * between sources and bails. The original /api/sync POST
   * that started the run will return with status="cancelled"
   * once the flag is picked up. Returns 404 if the job id
   * doesn't exist, 409 if the job has already finished.
   */
  cancelSync: (jobId: string) =>
    request<{ jobId: string; cancelled: boolean }>(
      `/api/sync/${jobId}/cancel`,
      { method: "POST" },
    ),
  /** GET /api/sync/history?limit=N — list recent sync runs. */
  getSyncHistory: (limit?: number) =>
    request<SyncLogEntry[]>(`/api/sync/history${limit ? `?limit=${limit}` : ""}`),

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
   *   - user just configured a provider for the first time
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
  /**
   * GET /api/distill/status — one-shot snapshot of the current distill
   * run. Same shape as the SSE stream's per-event payload, so the
   * frontend can use a single `DistillProgress` type for both the
   * initial poll and the live updates. When no run is in flight the
   * response is `{isRunning: false, ...}` — an "idle" state the UI
   * can render as a hidden progress bar.
   */
  getDistillStatus: () => request<DistillProgress>("/api/distill/status"),

  // ----- LLM provider settings (v0.2a) -----
  /**
   * GET /api/settings/providers — list of every supported provider with
   * the fields the UI should render and the default model. Mirrors the
   * `get_provider_schema` Tauri command, but the Tauri version is
   * preferred so the UI can be sure the schema matches the bundled
   * sidecar (no version skew).
   */
  listProviders: () => request<ProviderSchema[]>("/api/settings/providers"),
  /**
   * GET /api/settings/llm — current active provider and whether an API
   * key is configured. The key itself is NEVER returned. In the Tauri
   * build this hits the `get_llm_config` command which reads the
   * keychain slot; in pure Vite dev it falls back to the sidecar HTTP
   * endpoint.
   */
  getLlmConfig: async (): Promise<LlmConfig> => {
    if (isTauri()) return invoke<LlmConfig>("get_llm_config");
    return request<LlmConfig>("/api/settings/llm");
  },
  /**
   * Save the active LLM configuration.
   *
   * Two paths:
   *   - Tauri: invokes `set_llm_config` which writes the key into the
   *     OS keychain and asks Tauri to restart the sidecar (so the new
   *     env vars take effect). This is the only path that persists the
   *     API key.
   *   - Vite dev: POST to the sidecar HTTP endpoint. The sidecar updates
   *     its in-memory active provider; the key is NOT persisted
   *     anywhere (the keychain isn't accessible from the webview
   *     outside of Tauri). Good enough for UI iteration.
   */
   setLlmConfig: (update: LlmConfigUpdate): Promise<LlmConfig> => {
    if (isTauri()) {
      return invoke<LlmConfig>("set_llm_config", { config: update });
    }
    return request<LlmConfig>("/api/settings/llm", {
      method: "POST",
      body: JSON.stringify(update),
    });
  },

  // ----- Distill progress (SSE) ----------------------------------------
  /**
   * Subscribe to the distill progress stream. The browser's
   * `EventSource` auto-reconnects on transient errors; we just need
   * to forward each `data:` payload to the supplied callback.
   *
   * Returns a cleanup function that closes the underlying
   * connection. Use this in a `useEffect`'s teardown so the stream
   * is closed when the consuming component unmounts.
   *
   * The stream is sidecar-only — when we're running inside the
   * Tauri webview and the sidecar isn't up yet (e.g. first launch)
   * the EventSource will fail to connect; we swallow that and let
   * the caller decide whether to surface a fallback message.
   */
  subscribeDistillProgress(
    onProgress: (snap: DistillProgress) => void,
  ): () => void {
    const url = `${SIDECAR_BASE}/api/distill/status/stream`;
    const es = new EventSource(url);
    es.onmessage = (ev) => {
      try {
        const snap = JSON.parse(ev.data) as DistillProgress;
        onProgress(snap);
      } catch (err) {
        console.error("[prism] malformed distill progress event:", err, ev.data);
      }
    };
    es.onerror = () => {
      // EventSource auto-reconnects. We don't need to do anything
      // here; the next `onmessage` will resume. Logging once is
      // enough to make a stuck stream debuggable.
      console.warn("[prism] distill progress stream error; EventSource will retry");
    };
    return () => es.close();
  },
};

export { PrismAPIError, SIDECAR_BASE };
