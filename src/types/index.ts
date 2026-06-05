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
  title: string;
  url: string;
  author?: string;
  publishedAt: string;
  fetchedAt: string;
  status: ItemStatus;
  // Distilled content
  summary?: string;
  keyPoints?: string[];
  tags?: string[];
  // Metadata
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
