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
