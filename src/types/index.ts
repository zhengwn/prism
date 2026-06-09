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
 * Result of POST /api/sync. v0.2a runs synchronously, so the response only
 * lands after the whole pipeline finishes — `itemsNew` and
 * `itemsDistilled` give the user a useful summary in the toast.
 */
export interface SyncResult {
  jobId: string;
  startedAt: string;
  finishedAt: string;
  itemsNew: number;
  itemsDistilled: number;
}

/**
 * Status of an in-flight (or completed) sync job. Returned by
 * GET /api/sync/{jobId} and POST /api/sync/{sourceId}.
 */
export type SyncJobStatus = "pending" | "running" | "done" | "error";

export interface SyncJob {
  id: string;
  sourceId?: string;
  startedAt: string;
  finishedAt?: string;
  itemsNew: number;
  itemsDistilled: number;
  status: SyncJobStatus;
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
 * `configured: boolean`. Storing / clearing the key goes through the Tauri
 * keychain.
 */
export interface LlmConfig {
  provider: ProviderId;
  configured: boolean;
  model?: string;
  baseUrl?: string;
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
