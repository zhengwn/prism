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
  ItemStatus,
  KnowledgeItem,
  LlmConfig,
  LlmConfigUpdate,
  PrismHealth,
  ProviderSchema,
  SearchStatus,
  Source,
  SyncLogEntry,
  SyncResult,
  TagCount,
} from "@/types";

const SIDECAR_BASE = import.meta.env.VITE_PRISM_SIDECAR_URL ?? "http://127.0.0.1:8765";

class PrismAPIError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "PrismAPIError";
  }
}

/**
 * Loopback API token (v0.5.x). The Tauri shell generates a per-app-run
 * token, injects it into the sidecar's env, and exposes it via the
 * `get_sidecar_url` command; the sidecar rejects requests without it so
 * arbitrary local processes can't drive the API. Fetched lazily once
 * and cached (the token is stable for the whole app run, across
 * sidecar restarts). Pure-Vite dev has no Tauri shell AND no token env
 * on the manually-started sidecar, so both sides degrade to "no auth"
 * together.
 */
type SidecarInfo = { url: string; port: number; host: string; token?: string | null };
let sidecarTokenPromise: Promise<string | null> | null = null;
function getSidecarToken(): Promise<string | null> {
  if (!isTauri()) return Promise.resolve(null);
  if (!sidecarTokenPromise) {
    sidecarTokenPromise = invoke<SidecarInfo>("get_sidecar_url")
      .then((info) => info.token ?? null)
      .catch(() => null);
  }
  return sidecarTokenPromise;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await getSidecarToken();
  const res = await fetch(`${SIDECAR_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Prism-Token": token } : {}),
      ...(init?.headers ?? {}),
    },
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
export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export const api = {
  // ----- Health -----
  health: () => request<PrismHealth>("/health"),

  // ----- Sources -----
  listSources: () => request<Source[]>("/api/sources"),
  getSource: (id: string) => request<Source>(`/api/sources/${id}`),
  /**
   * Create a source. The body keys must match the sidecar's
   * `SourceCreate` model (camelCase aliases): `name`, `kind`, `url`,
   * `enabled`, `configJson`. Anything else is silently ignored by
   * Pydantic — which is exactly how the old top-level `bvid` field
   * got dropped and Bilibili sources ended up with an empty
   * `config_json` (and synced nothing). For `kind: "bilibili"` the
   * caller MUST put `{ bvid }` or `{ mid }` into `configJson`; the
   * `BilibiliFetcher` dispatches on that bag alone.
   */
  createSource: (
    data: Omit<Source, "id" | "itemCount" | "lastSyncedAt" | "lastError">,
  ) =>
    request<Source>("/api/sources", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  patchSource: (
    id: string,
    data: Partial<Pick<Source, "name" | "url" | "enabled" | "configJson">>,
  ) =>
    request<Source>(`/api/sources/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteSource: (id: string) =>
    request<{ ok: true }>(`/api/sources/${id}`, { method: "DELETE" }),

  // ----- Items -----
  /**
   * GET /api/items — server-side filtering. The query string keys MUST
   * match the FastAPI parameter names exactly (`source_id`, `status`,
   * `q`, `limit`, `offset`) — FastAPI does not camelCase-alias query
   * params, so `?sourceId=` would silently be ignored and the caller
   * would get an unfiltered page back. `limit` defaults to the
   * backend's max (200) since the UI doesn't yet have pagination /
   * "load more"; without it the backend's own default (50) would
   * silently truncate the list before the source/status filters below
   * even apply.
   */
  listItems: (params?: {
    sourceId?: string;
    status?: string;
    q?: string;
    tag?: string;
    limit?: number;
    offset?: number;
  }) => {
    const search = new URLSearchParams();
    if (params?.sourceId) search.set("source_id", params.sourceId);
    if (params?.status && params.status !== "all") search.set("status", params.status);
    if (params?.q) search.set("q", params.q);
    if (params?.tag) search.set("tag", params.tag);
    search.set("limit", String(params?.limit ?? 200));
    if (params?.offset) search.set("offset", String(params.offset));
    const qs = search.toString();
    return request<KnowledgeItem[]>(`/api/items${qs ? `?${qs}` : ""}`);
  },
  getItem: (id: string) => request<KnowledgeItem>(`/api/items/${id}`),
  /**
   * PATCH /api/items/{id} — set the read/starred/archived status.
   * This is the write path behind the inbox status filters.
   */
  updateItemStatus: (id: string, status: ItemStatus) =>
    request<KnowledgeItem>(`/api/items/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),

  // ----- User tags (v0.5) -----
  /** GET /api/tags — every user tag with its item count. */
  listTags: () => request<TagCount[]>("/api/tags"),
  /** POST /api/items/{id}/tags — attach a user tag (idempotent). */
  addItemTag: (id: string, tag: string) =>
    request<KnowledgeItem>(`/api/items/${id}/tags`, {
      method: "POST",
      body: JSON.stringify({ tag }),
    }),
  /** DELETE /api/items/{id}/tags/{tag} — remove a user tag (idempotent). */
  removeItemTag: (id: string, tag: string) =>
    request<KnowledgeItem>(
      `/api/items/${id}/tags/${encodeURIComponent(tag)}`,
      { method: "DELETE" },
    ),

  // ----- Semantic search (v0.5) -----
  /** GET /api/search/status — availability + indexed/pending counts. */
  searchStatus: () => request<SearchStatus>("/api/search/status"),
  /** POST /api/search/reindex — embed distilled items missing a vector. */
  reindexSemantic: (batchLimit?: number) =>
    request<{ available: boolean; indexed: number; failed: number; remaining: number }>(
      `/api/search/reindex${batchLimit ? `?batch_limit=${batchLimit}` : ""}`,
      { method: "POST" },
    ),
  /**
   * GET /api/search/semantic — nearest items to `q` by embedding. Returns
   * an empty list when semantic search is unavailable (the caller should
   * fall back to the FTS `listItems`).
   */
  semanticSearch: (params: {
    q: string;
    limit?: number;
    sourceId?: string;
    status?: string;
  }) => {
    const search = new URLSearchParams();
    search.set("q", params.q);
    search.set("limit", String(params.limit ?? 200));
    if (params.sourceId) search.set("source_id", params.sourceId);
    if (params.status && params.status !== "all") search.set("status", params.status);
    return request<KnowledgeItem[]>(`/api/search/semantic?${search.toString()}`);
  },

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
  /** GET /api/sync/history?limit=N — list recent per-source sync log rows. */
  getSyncHistory: (limit?: number) =>
    request<SyncLogEntry[]>(`/api/sync/history${limit ? `?limit=${limit}` : ""}`),
  /**
   * GET /api/sync/jobs?limit=N — recent sync RUNS (aggregated per job,
   * newest first). The notification hook polls this to detect a background
   * sync that brought in new items.
   */
  getSyncJobs: (limit?: number) =>
    request<SyncResult[]>(`/api/sync/jobs${limit ? `?limit=${limit}` : ""}`),

  // ----- Distill -----
  /**
   * GET /api/distill/pending-count — how many items are waiting to be
   * distilled. Used by the Settings "重蒸馏所有 pending" button to show
   * the user how much work is queued.
   */
  getPendingDistillCount: () =>
    request<{ pending: number }>("/api/distill/pending-count"),
  /**
   * POST /api/distill/redistill — start a BACKGROUND re-distill of every
   * item with `distilled_at IS NULL`. Use cases:
   *   - user just configured a provider for the first time
   *   - user's key expired / ran out and they want a clean re-run
   * v0.5.x: returns immediately with `background: true` and
   * `startedPending` set (a big batch is hours of serial LLM calls — no
   * HTTP request should sit on that). Live counters and the final
   * outcome (including a mid-run key-invalid, via `lastError`) come
   * through the distill progress stream / GET /api/distill/status.
   */
  redistill: () =>
    request<{
      startedPending: number;
      distilled: number;
      failed: number;
      keyInvalid: boolean;
      error?: string;
      sampleFailures: string[];
      background: boolean;
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
   * Manually restart the Python sidecar (Tauri only).
   *
   * The Tauri command fires a background kill+respawn and returns
   * immediately; callers should poll `health()` (version/uptime reset)
   * to detect the new process. Outside the Tauri shell there is no
   * child process to restart, so this is a no-op that resolves — the
   * sidecar is managed externally (`npm run sidecar:dev`) in that case.
   */
  restartSidecar: async (): Promise<void> => {
    if (isTauri()) {
      await invoke("restart_sidecar");
      return;
    }
    console.warn(
      "[prism] restartSidecar is a no-op in browser dev; the sidecar is " +
      "managed externally (npm run sidecar:dev).",
    );
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
    // Vite dev fallback. The sidecar HTTP endpoint rejects any body
    // containing `apiKey` with a 400 ("keys never transit HTTP") — the
    // old code forwarded the field anyway, so saving a key in pure-Vite
    // dev always produced the red error toast (ROADMAP: "Vite 调试下
    // setApiKey 抛错"). Strip it and warn instead: provider/model
    // changes still apply; key persistence genuinely needs Tauri.
    const { apiKey, ...rest } = update;
    if (apiKey) {
      console.warn(
        "[prism] API key can only be saved from the Tauri app (local keystore); " +
        "ignoring the key in browser dev. Provider/model changes still apply.",
      );
    }
    return request<LlmConfig>("/api/settings/llm", {
      method: "POST",
      body: JSON.stringify(rest),
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
    // EventSource cannot set custom headers, so the loopback token
    // rides in the query string (the sidecar's auth middleware accepts
    // either). Token fetch is async — the stream opens right after it
    // resolves; `closed` covers an unmount racing that resolution.
    let es: EventSource | null = null;
    let closed = false;
    void getSidecarToken().then((token) => {
      if (closed) return;
      const qs = token ? `?token=${encodeURIComponent(token)}` : "";
      es = new EventSource(`${SIDECAR_BASE}/api/distill/status/stream${qs}`);
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
    });
    return () => {
      closed = true;
      es?.close();
    };
  },
};

export { PrismAPIError, SIDECAR_BASE };
