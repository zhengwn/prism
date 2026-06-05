/**
 * Prism API client.
 *
 * In dev: hits the Python sidecar directly at http://127.0.0.1:8765
 * In prod (Tauri): the Rust sidecar is spawned automatically and we hit it the same way.
 *
 * In a future version we'll route through Tauri commands to avoid CORS / port pinning,
 * but for v0.1 the sidecar is on a fixed loopback port and the Tauri webview is allowed
 * to talk to it via the `localhost` capability.
 */

import type { KnowledgeItem, PrismHealth, Source } from "@/types";

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

export const api = {
  health: () => request<PrismHealth>("/health"),

  listSources: () => request<Source[]>("/api/sources"),
  getSource: (id: string) => request<Source>(`/api/sources/${id}`),
  createSource: (data: Omit<Source, "id" | "itemCount" | "lastSyncedAt">) =>
    request<Source>("/api/sources", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  deleteSource: (id: string) =>
    request<{ ok: true }>(`/api/sources/${id}`, { method: "DELETE" }),

  listItems: (params?: { sourceId?: string; status?: string; q?: string }) => {
    const search = new URLSearchParams();
    if (params?.sourceId) search.set("sourceId", params.sourceId);
    if (params?.status) search.set("status", params.status);
    if (params?.q) search.set("q", params.q);
    const qs = search.toString();
    return request<KnowledgeItem[]>(`/api/items${qs ? `?${qs}` : ""}`);
  },
  getItem: (id: string) => request<KnowledgeItem>(`/api/items/${id}`),

  syncAll: () => request<{ triggered: number }>("/api/sync", { method: "POST" }),
};

export { PrismAPIError, SIDECAR_BASE };
