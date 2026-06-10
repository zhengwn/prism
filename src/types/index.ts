/**
 * Prism data types — shared between frontend and Python sidecar.
 * Keep these in sync with `python/prism_sidecar/models.py`.
 */

export type SourceKind = "rss" | "youtube" | "podcast" | "blog" | "x" | "pdf" | "file";

export interface Source {
  id: string;
  name: string;
  kind: SourceKind;
  url: string;
  enabled: boolean;
  lastSyncedAt?: string;
  itemCount: number;
}

export type ItemStatus = "unread" | "read" | "archived" | "starred";

export interface KnowledgeItem {
  id: string;
  sourceId: string;
  sourceName: string;
  url: string;
  // Bilingual fields — v0.2a. The `_en` field is the original; the `_zh`
  // field is filled in by the distiller (DeepSeek in v0.2a). Pre-distilled
  // items only have the `_en` field populated; the back-compat `title` /
  // `summary` / `keyPoints` / `tags` below always carry a displayable value
  // (zh if available, otherwise en).
  titleEn: string;
  titleZh?: string;
  summaryEn?: string;
  summaryZh?: string;
  keyPointsZh?: string[];
  tagsZh?: string[];
  // Back-compat display fields — backend returns titleZh ?? titleEn (and
  // summaryZh ?? summaryEn, keyPointsZh ?? [], tagsZh ?? []) for these so
  // older clients still render correctly. New code should prefer the
  // explicit `_en` / `_zh` fields and pick the active locale.
  title: string;
  summary?: string;
  keyPoints?: string[];
  tags?: string[];
  // Status / meta
  author?: string;
  publishedAt: string;
  fetchedAt: string;
  /**
   * ISO timestamp of when the distiller finished, or undefined if the
   * item is still raw. UI can use this to show a "待提炼" badge.
   */
  distilledAt?: string;
  status: ItemStatus;
  durationSec?: number;
  contentType: "video" | "audio" | "article" | "paper" | "post";
}

export interface PrismHealth {
  ok: boolean;
  version: string;
  sourcesCount: number;
  itemsCount: number;
  uptimeSec: number;
}

/**
 * Result of POST /api/sync. v0.2b returns immediately with
 * `status="running"` and the pipeline runs in the background —
 * the inbox polls GET /api/sync/{jobId} until `status` flips to
 * one of the terminal values (done / error / cancelled). The
 * shape matches the sidecar's `SyncResult` Pydantic model and
 * is also used as the type for the polled intermediate states.
 */
export interface SyncResult {
  jobId: string;
  sourceId?: string;
  startedAt: string;
  finishedAt?: string | null;
  status: SyncJobStatus;
  sourcesTotal: number;
  sourcesDone: number;
  itemsNew: number;
  itemsDistilled: number;
  error?: string | null;
}

/**
 * Status of an in-flight (or completed) sync job. v0.2b added
 * "cancelled" so the UI can distinguish a user-initiated stop
 * from a pipeline error.
 */
export type SyncJobStatus = "pending" | "running" | "done" | "error" | "cancelled";

/**
 * One entry from GET /api/sync/history. v0.2b: shape mirrors the
 * sidecar's `SyncLogEntry` model (one row per source, NOT per
 * job). If we ever need a true per-job history view we'll add a
 * separate endpoint rather than overloading this one.
 */
export interface SyncLogEntry {
  id: number;
  sourceId?: string;
  startedAt: string;
  finishedAt?: string;
  itemsNew: number;
  itemsDistilled: number;
  error?: string;
}

// ---------- v0.2a — LLM provider settings ----------

/**
 * The set of providers the sidecar supports. Adding a new provider requires
 * updating both the Python registry (`python/prism_sidecar/distillers/`) and
 * the i18n hint strings in `src/i18n/{en,zh}.json`.
 */
export type ProviderId = "deepseek" | "minimax";

/**
 * A single configurable field on a provider's settings form. The `name` is
 * the machine key (the API field the sidecar expects); the rest is i18n / UX.
 */
export interface ProviderField {
  name: "api_key" | "model" | "base_url";
  label: string;
  required: boolean;
  default?: string;
  placeholder?: string;
}

/**
 * Schema describing one LLM provider, returned by GET /api/settings/providers
 * and the `get_provider_schema` Tauri command. The UI uses this to know which
 * fields to render and what placeholders / hints to show.
 */
export interface ProviderSchema {
  id: ProviderId;
  label: string;
  hint: string;
  requiresKey: boolean;
  defaultModel: string;
  fields: ProviderField[];
}

/**
 * The currently active LLM configuration. Returned by GET /api/settings/llm
 * and `get_llm_config`. The API key is NEVER returned — the UI only knows
 * `configured: boolean` plus the trailing 4 chars (`keyLast4`) and the
 * total length (`keyLength`) so the Settings page can render a
 * length-matched password mask inside the password input. Storing /
 * clearing the key goes through the Tauri keystore.
 */
export interface LlmConfig {
  provider: ProviderId;
  configured: boolean;
  keyLast4?: string;
  keyLength?: number;
  model?: string;
  baseUrl?: string;
}

/**
 * Live progress snapshot for the distill pipeline. Returned by
 * `GET /api/distill/status` and emitted as `data:` events on
 * `GET /api/distill/status/stream` (Server-Sent Events).
 *
 * Shape notes:
 * - `isRunning` is the master switch — when `false`, the UI hides
 *   the progress bar and stops animating.
 * - `pending` is the best-effort count set by the run-start
 *   (known for redistill; 0 for sync, where the exact count isn't
 *   known until each source is fetched). The UI should treat
 *   `pending === 0` as "indeterminate" and show a spinner without
 *   a percentage.
 * - `distilled` + `failed` are running counters — they only ever
 *   go up during a run.
 * - `currentTitle` / `currentSource` describe the item currently
 *   being distilled, so the UI can show "正在蒸馏: <title>"
 *   instead of an anonymous counter.
 * - `lastError` carries a short error tag (e.g. "key_invalid")
 *   when the run ended unsuccessfully. The UI uses it to show a
 *   useful toast.
 */
export interface DistillProgress {
  isRunning: boolean;
  pending: number;
  distilled: number;
  failed: number;
  currentTitle?: string | null;
  currentSource?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  lastEventAt?: number;
  lastError?: string | null;
}

/**
 * Payload for `setLlmConfig` — what the Settings UI sends to save a new
 * provider. All fields except `provider` are optional because the user may
 * only be flipping the dropdown (in which case the existing key / model /
 * base_url stay intact).
 */
export interface LlmConfigUpdate {
  provider: ProviderId;
  apiKey?: string;
  model?: string;
  baseUrl?: string;
}
