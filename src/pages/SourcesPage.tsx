import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Rss, Plus, Trash2, ExternalLink, Power, X, Loader2 } from "lucide-react";
import type { Source, SourceKind } from "@/types";
import { formatRelativeTime, cn } from "@/lib/utils";
import { useLanguage } from "@/hooks/useLanguage";

const kindIcon: Record<SourceKind, string> = {
  rss: "📡",
  youtube: "▶️",
  podcast: "🎙️",
  blog: "📝",
  x: "✕",
  pdf: "📄",
  file: "📁",
  bilibili: "📺",
  arxiv: "📜",
};

const KIND_OPTIONS: SourceKind[] = [
  "rss",
  "blog",
  "youtube",
  "podcast",
  "arxiv",
  "x",
  "pdf",
  "file",
  "bilibili",
];

/**
 * Strip a `BVxxxxxxxxxx` token out of a URL or a bare id. B station
 * source urls come in three flavours (mid page, BV url, bare BV id)
 * — for the badge on the card we just want a stable token we can
 * truncate for display.
 */
function bilibiliHint(url: string): string | null {
  if (!url) return null;
  const bv = url.match(/BV[0-9A-Za-z]{8,}/i);
  if (bv) return bv[0];
  // mid page: https://space.bilibili.com/339137722
  const mid = url.match(/space\.bilibili\.com\/(\d+)/i);
  if (mid) return `mid ${mid[1]}`;
  return null;
}

/**
 * Build the `configJson` bag for a new Bilibili source from the URL the
 * user pasted. The BilibiliFetcher dispatches on `config_json.bvid`
 * (single video) or `config_json.mid` (UP 主 submissions) — a bilibili
 * source with an empty bag syncs nothing, so this is the load-bearing
 * part of the create call.
 */
function bilibiliConfigFromUrl(url: string): Record<string, unknown> | undefined {
  const bv = url.match(/BV[0-9A-Za-z]{8,}/i);
  if (bv) return { bvid: bv[0] };
  const mid = url.match(/space\.bilibili\.com\/(\d+)/i);
  if (mid) return { mid: mid[1] };
  return undefined;
}

/** Read the display hint (BV id / mid) off a source, preferring the
 * authoritative `configJson` and falling back to URL parsing for
 * sources created before configJson was populated. */
function bilibiliSourceHint(source: Source): string | null {
  const cfg = source.configJson ?? {};
  if (typeof cfg.bvid === "string" && cfg.bvid) return cfg.bvid;
  if (typeof cfg.mid === "string" || typeof cfg.mid === "number") return `mid ${cfg.mid}`;
  return bilibiliHint(source.url);
}

/**
 * Build the `configJson` bag for a new YouTube source from what the
 * user pasted. The YouTubeFetcher dispatches on `config_json.channel`
 * (channel uploads) or `config_json.video` (single video) — same
 * load-bearing role as `bilibiliConfigFromUrl` above.
 *
 * Accepted forms: @handle, channel URL (youtube.com/@handle,
 * /channel/UC…), watch URL, youtu.be short URL, bare 11-char video id.
 */
function youtubeConfigFromUrl(url: string): Record<string, unknown> | undefined {
  const v = url.trim();
  if (!v) return undefined;
  const watch = v.match(/(?:[?&]v=|youtu\.be\/)([A-Za-z0-9_-]{11})/);
  if (watch) return { video: watch[1] };
  if (/^[A-Za-z0-9_-]{11}$/.test(v)) return { video: v };
  if (v.startsWith("@")) return { channel: v };
  const handle = v.match(/youtube\.com\/(@[^/?#]+)/i);
  if (handle) return { channel: handle[1] };
  const channelId = v.match(/youtube\.com\/channel\/(UC[A-Za-z0-9_-]{10,})/i);
  if (channelId) return { channel: channelId[1] };
  if (/^UC[A-Za-z0-9_-]{10,}$/.test(v)) return { channel: v };
  return undefined;
}

/**
 * Build the `configJson` bag for a new arXiv source. The ArxivFetcher
 * reads `config_json.categories`; the "URL" field doubles as the
 * category input (comma-separated, e.g. "cs.AI, cs.CL"). An arxiv.org
 * URL or empty input falls back to the fetcher's defaults.
 */
function arxivConfigFromInput(input: string): Record<string, unknown> | undefined {
  const v = input.trim();
  if (!v || /arxiv\.org/i.test(v)) return undefined; // fetcher defaults
  const cats = v
    .split(",")
    .map((c) => c.trim())
    .filter((c) => /^[a-z-]+(\.[A-Za-z-]+)?$/.test(c));
  return cats.length ? { categories: cats } : undefined;
}

/** Display hint for a YouTube source card (channel handle / video id). */
function youtubeSourceHint(source: Source): string | null {
  const cfg = source.configJson ?? {};
  if (typeof cfg.channel === "string" && cfg.channel) return cfg.channel;
  if (typeof cfg.video === "string" && cfg.video) return cfg.video;
  const parsed = youtubeConfigFromUrl(source.url ?? "");
  if (parsed?.channel) return String(parsed.channel);
  if (parsed?.video) return String(parsed.video);
  return null;
}

/**
 * Build the `configJson` bag for a new X (Twitter) source.
 *
 * X has no free/stable no-auth timeline API, so the PoC XFetcher consumes
 * a *bridge feed* (self-hosted RSSHub `/twitter/user/:handle` or a Nitter
 * `/:handle/rss`). The single URL field therefore expects a full bridge
 * feed URL → stored as `config_json.feed_url` (the fetcher's highest-
 * precedence input). A bare @handle / x.com profile URL can't be fetched
 * without knowing the user's bridge host, so we leave the bag empty and
 * let the sidecar surface a `retryable=False` "needs config_json.bridge"
 * error in `sources.last_error`.
 */
function xConfigFromUrl(url: string): Record<string, unknown> | undefined {
  const v = url.trim();
  if (!v) return undefined;
  const isDirectFeed =
    /^https?:\/\//i.test(v) && !/(^|\/\/|\.)((x)|(twitter))\.com(\/|$)/i.test(v);
  return isDirectFeed ? { feed_url: v } : undefined;
}

/** Extract a display handle for an X source card, if one is discoverable
 * from the stored feed URL (`…/twitter/user/<handle>` or `…/<handle>/rss`). */
function xSourceHint(source: Source): string | null {
  const cfg = source.configJson ?? {};
  const feed =
    (typeof cfg.feed_url === "string" && cfg.feed_url) || source.url || "";
  const user = feed.match(/twitter\/user\/([A-Za-z0-9_]{1,15})/i);
  if (user) return `@${user[1]}`;
  const nitter = feed.match(/\/([A-Za-z0-9_]{1,15})\/rss(?:$|[?#])/i);
  if (nitter) return `@${nitter[1]}`;
  const handle = source.url?.match(/^@?([A-Za-z0-9_]{1,15})$/);
  if (handle) return `@${handle[1]}`;
  return null;
}

export function SourcesPage() {
  const { data: sources, isLoading } = useQuery({
    queryKey: ["sources"],
    queryFn: () => api.listSources(),
  });

  const qc = useQueryClient();
  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteSource(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sources"] });
      qc.invalidateQueries({ queryKey: ["items"] });
    },
  });
  const { t } = useLanguage();

  const [addOpen, setAddOpen] = useState(false);

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">{t("sources.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("sources.description")}</p>
        </div>
        <Button className="gap-1.5" onClick={() => setAddOpen(true)} data-testid="add-source-button">
          <Plus className="h-4 w-4" />
          {t("sources.addSource")}
        </Button>
      </div>

      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : sources?.length === 0 ? (
        <EmptySourcesState />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {sources?.map((src) => (
            <SourceCard
              key={src.id}
              source={src}
              onDelete={() => deleteMut.mutate(src.id)}
              deleting={deleteMut.isPending}
            />
          ))}
        </div>
      )}

      <AddSourceDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        onCreated={() => {
          qc.invalidateQueries({ queryKey: ["sources"] });
          qc.invalidateQueries({ queryKey: ["items"] });
        }}
      />
    </div>
  );
}

function SourceCard({
  source,
  onDelete,
  deleting,
}: {
  source: Source;
  onDelete: () => void;
  deleting: boolean;
}) {
  const qc = useQueryClient();
  const { t } = useLanguage();
  const toggleMut = useMutation({
    mutationFn: (next: boolean) => api.patchSource(source.id, { enabled: next }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xl">{kindIcon[source.kind]}</span>
            <div>
              <CardTitle className="text-sm">{source.name}</CardTitle>
              <CardDescription className="text-xs">
                <a
                  href={source.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 hover:underline"
                >
                  {safeHostname(source.url)}
                  <ExternalLink className="h-2.5 w-2.5" />
                </a>
              </CardDescription>
            </div>
          </div>
          <Badge variant={source.enabled ? "default" : "secondary"}>
            {source.enabled ? t("sources.active") : t("sources.paused")}
          </Badge>
          {source.kind === "bilibili" && (
            <Badge
              variant="outline"
              className="ml-1 gap-0.5 border-pink-500/40 bg-pink-500/10 text-[10px] text-pink-700 dark:text-pink-300"
              data-testid="source-bilibili-badge"
            >
              {t("sources.addDialog.badgeBilibili")}
              {(() => {
                const hint = bilibiliSourceHint(source);
                return hint ? <span className="ml-1 opacity-70">· {hint}</span> : null;
              })()}
            </Badge>
          )}
          {source.kind === "youtube" && (
            <Badge
              variant="outline"
              className="ml-1 gap-0.5 border-red-500/40 bg-red-500/10 text-[10px] text-red-700 dark:text-red-300"
              data-testid="source-youtube-badge"
            >
              {t("sources.addDialog.badgeYoutube")}
              {(() => {
                const hint = youtubeSourceHint(source);
                return hint ? <span className="ml-1 opacity-70">· {hint}</span> : null;
              })()}
            </Badge>
          )}
          {source.kind === "x" && (
            <Badge
              variant="outline"
              className="ml-1 gap-0.5 border-neutral-500/40 bg-neutral-500/10 text-[10px] text-neutral-700 dark:text-neutral-300"
              data-testid="source-x-badge"
            >
              {t("sources.addDialog.badgeX")}
              {(() => {
                const hint = xSourceHint(source);
                return hint ? <span className="ml-1 opacity-70">· {hint}</span> : null;
              })()}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {t("sources.itemsCount", { count: source.itemCount })} · {t("sources.lastSynced")}{" "}
            {source.lastSyncedAt ? formatRelativeTime(source.lastSyncedAt, t) : t("sources.lastSyncedNever")}
          </span>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground hover:text-foreground"
              onClick={() => toggleMut.mutate(!source.enabled)}
              disabled={toggleMut.isPending}
              title={source.enabled ? "Pause" : "Resume"}
              aria-label={source.enabled ? "Pause" : "Resume"}
              data-testid="toggle-source"
            >
              <Power className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground hover:text-destructive"
              onClick={onDelete}
              disabled={deleting}
              aria-label="Delete"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function AddSourceDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: () => void;
}) {
  const { t } = useLanguage();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<SourceKind>("rss");
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

  // B station sources can be added either as a UP 主 mid page
  // (https://space.bilibili.com/<mid>) or a single BV url
  // (https://www.bilibili.com/video/<bvid>) or a bare BV id.
  // We accept all three forms; the sidecar parses out the id.
  const urlPlaceholder =
    kind === "bilibili"
      ? t("sources.addDialog.urlPlaceholderBilibili")
      : kind === "youtube"
        ? t("sources.addDialog.urlPlaceholderYoutube")
        : kind === "arxiv"
          ? t("sources.addDialog.urlPlaceholderArxiv")
          : kind === "x"
            ? t("sources.addDialog.urlPlaceholderX")
            : t("sources.addDialog.urlPlaceholder");

  const createMut = useMutation({
    mutationFn: () => {
      // For bilibili sources, parse the pasted URL (BV url / bare BV
      // id / mid page) into the `configJson` bag the fetcher actually
      // reads. Sent as `configJson` — the camelCase alias of the
      // sidecar's `Source.config_json`; a top-level `bvid` key would
      // be silently dropped by Pydantic.
      const configJson =
        kind === "bilibili"
          ? bilibiliConfigFromUrl(url)
          : kind === "youtube"
            ? youtubeConfigFromUrl(url)
            : kind === "arxiv"
              ? arxivConfigFromInput(url)
              : kind === "x"
                ? xConfigFromUrl(url)
                : undefined;
      // arXiv's "URL" field holds categories, not a link — store a
      // stable arxiv.org URL so the card's hostname link stays sane.
      const effectiveUrl =
        kind === "arxiv" && !/^https?:\/\//i.test(url.trim())
          ? "https://arxiv.org"
          : url;
      return api.createSource({
        name,
        kind,
        url: effectiveUrl,
        enabled: true,
        ...(configJson ? { configJson } : {}),
      });
    },
    onSuccess: () => {
      setName("");
      setUrl("");
      setKind("rss");
      setError(null);
      onOpenChange(false);
      onCreated();
    },
    onError: (e) => {
      console.error("[prism] createSource failed:", e);
      setError(t("sources.addDialog.error"));
    },
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !url.trim()) {
      setError(t("sources.addDialog.error"));
      return;
    }
    setError(null);
    createMut.mutate();
  };

  const onClose = () => {
    if (createMut.isPending) return;
    setError(null);
    onOpenChange(false);
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
      data-testid="add-source-dialog"
    >
      <div
        className={cn(
          "w-full max-w-md rounded-lg border bg-card text-card-foreground shadow-lg",
          "animate-in fade-in-0 zoom-in-95",
        )}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-source-title"
      >
        <form onSubmit={onSubmit}>
          <div className="flex items-center justify-between border-b p-4">
            <h3 id="add-source-title" className="text-sm font-semibold">
              {t("sources.addDialog.title")}
            </h3>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={onClose}
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>

          <div className="space-y-3 p-4">
            <div className="space-y-1.5">
              <label htmlFor="source-name" className="text-xs font-medium text-muted-foreground">
                {t("sources.addDialog.name")}
              </label>
              <Input
                id="source-name"
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={
                  kind === "bilibili"
                    ? t("sources.addDialog.bilibiliPlaceholder")
                    : t("sources.addDialog.namePlaceholder")
                }
                disabled={createMut.isPending}
                data-testid="add-source-name"
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="source-kind" className="text-xs font-medium text-muted-foreground">
                {t("sources.addDialog.kind")}
              </label>
              <select
                id="source-kind"
                value={kind}
                onChange={(e) => setKind(e.target.value as SourceKind)}
                disabled={createMut.isPending}
                data-testid="add-source-kind"
                className={cn(
                  "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm",
                  "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                  "disabled:cursor-not-allowed disabled:opacity-50",
                )}
              >
                {KIND_OPTIONS.map((k) => (
                  <option key={k} value={k}>
                    {kindLabel(k, t)}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label htmlFor="source-url" className="text-xs font-medium text-muted-foreground">
                {t("sources.addDialog.url")}
              </label>
              <Input
                id="source-url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder={urlPlaceholder}
                disabled={createMut.isPending}
                data-testid="add-source-url"
              />
            </div>

            {error && (
              <p className="text-xs text-destructive" role="alert">
                {error}
              </p>
            )}
          </div>

          <div className="flex items-center justify-end gap-2 border-t p-4">
            <Button
              type="button"
              variant="ghost"
              onClick={onClose}
              disabled={createMut.isPending}
            >
              {t("sources.addDialog.cancel")}
            </Button>
            <Button type="submit" disabled={createMut.isPending} data-testid="add-source-submit">
              {createMut.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {t("sources.addDialog.submit")}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function kindLabel(k: SourceKind, t: (key: string) => string): string {
  const map: Record<SourceKind, string> = {
    rss: t("sources.addDialog.kindRss"),
    blog: t("sources.addDialog.kindBlog"),
    youtube: t("sources.addDialog.kindYoutube"),
    podcast: t("sources.addDialog.kindPodcast"),
    x: t("sources.addDialog.kindX"),
    pdf: t("sources.addDialog.kindPdf"),
    file: t("sources.addDialog.kindFile"),
    bilibili: t("sources.addDialog.kindBilibili"),
    arxiv: t("sources.addDialog.kindArxiv"),
  };
  return map[k];
}

function safeHostname(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

function EmptySourcesState() {
  const { t } = useLanguage();
  return (
    <div className="flex h-64 items-center justify-center rounded-lg border border-dashed">
      <div className="max-w-sm text-center space-y-2">
        <Rss className="mx-auto h-8 w-8 text-muted-foreground" />
        <p className="text-sm font-medium">{t("sources.emptyTitle")}</p>
        <p className="text-xs text-muted-foreground">{t("sources.emptyDescription")}</p>
      </div>
    </div>
  );
}
