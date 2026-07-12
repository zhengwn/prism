import { useEffect, useState } from "react";
import { X, ExternalLink, Sparkles, Hash, PlayCircle, Star, Archive } from "lucide-react";
import { usePrismStore } from "@/store";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatRelativeTime, cn } from "@/lib/utils";
import { useLanguage } from "@/hooks/useLanguage";
import { parseInline, splitParagraphs } from "@/lib/inline-markdown";
import type { ItemStatus, KnowledgeItem } from "@/types";

type Lang = "zh" | "en";

/**
 * Pick the title/summary to render for the current language preference.
 * The `force` argument lets the explicit EN/中 toggle in the header
 * override the UI language.
 */
function pickLocalized(
  item: KnowledgeItem,
  lang: Lang,
): { title: string; summary?: string; keyPoints: string[]; tags: string[] } {
  if (lang === "en") {
    return {
      title: item.titleEn || item.title,
      summary: item.summaryEn ?? item.summary,
      keyPoints: item.keyPoints ?? item.keyPointsZh ?? [],
      tags: item.tagsZh ?? item.tags ?? [],
    };
  }
  return {
    title: item.titleZh || item.titleEn || item.title,
    summary: item.summaryZh ?? item.summaryEn ?? item.summary,
    keyPoints: item.keyPointsZh ?? item.keyPoints ?? [],
    tags: item.tagsZh ?? item.tags ?? [],
  };
}

export function DetailPanel() {
  const selectedItemId = usePrismStore((s) => s.selectedItemId);
  const setSelectedItem = usePrismStore((s) => s.setSelectedItem);
  const { t, language } = useLanguage();

  /**
   * The active language for the item preview. Defaults to the UI
   * language but the user can flip it with the EN / 中 buttons to peek
   * at the other version.
   */
  const [overrideLang, setOverrideLang] = useState<Lang | null>(null);
  const activeLang: Lang = overrideLang ?? (language === "en" ? "en" : "zh");

  const qc = useQueryClient();
  const { data: item, isLoading } = useQuery({
    queryKey: ["item", selectedItemId],
    queryFn: () => api.getItem(selectedItemId!),
    enabled: !!selectedItemId,
  });

  const statusMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ItemStatus }) =>
      api.updateItemStatus(id, status),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["item", selectedItemId] });
      qc.invalidateQueries({ queryKey: ["items"] });
    },
  });

  // v0.5: user-tag add/remove. Both invalidate the item (to refresh its
  // chips), the item list (a tag filter may now include/exclude it), and
  // the tag list (the inbox rail's counts).
  const [tagInput, setTagInput] = useState("");
  const invalidateTags = () => {
    qc.invalidateQueries({ queryKey: ["item", selectedItemId] });
    qc.invalidateQueries({ queryKey: ["items"] });
    qc.invalidateQueries({ queryKey: ["tags"] });
  };
  const addTagMut = useMutation({
    mutationFn: ({ id, tag }: { id: string; tag: string }) => api.addItemTag(id, tag),
    onSuccess: invalidateTags,
  });
  const removeTagMut = useMutation({
    mutationFn: ({ id, tag }: { id: string; tag: string }) => api.removeItemTag(id, tag),
    onSuccess: invalidateTags,
  });
  const submitTag = (id: string) => {
    const tag = tagInput.trim();
    if (!tag) return;
    addTagMut.mutate({ id, tag });
    setTagInput("");
  };

  // Opening an unread item marks it read — this is what makes the
  // inbox's "unread" filter mean something. Starred/archived items
  // are left alone (those are explicit user choices).
  useEffect(() => {
    if (item && item.status === "unread") {
      statusMut.mutate({ id: item.id, status: "read" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id, item?.status]);

  if (!selectedItemId) {
    return (
      <aside className="hidden h-full w-80 shrink-0 flex-col border-l bg-card/30 lg:flex">
        <div className="flex h-full items-center justify-center p-6 text-center">
          <div className="space-y-2">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full prism-gradient">
              <Sparkles className="h-5 w-5 text-white" />
            </div>
            <p className="text-sm font-medium">{t("detail.selectTitle")}</p>
            <p className="text-xs text-muted-foreground">{t("detail.selectDescription")}</p>
          </div>
        </div>
      </aside>
    );
  }

  const localized = item ? pickLocalized(item, activeLang) : null;
  const hasBothLanguages = item
    ? Boolean(item.titleEn) && Boolean(item.titleZh) &&
      (item.titleEn !== item.titleZh)
    : false;
  const hasEn = item ? Boolean(item.titleEn) : false;
  const hasZh = item ? Boolean(item.titleZh) : false;

  return (
    <aside className="hidden h-full w-96 shrink-0 flex-col border-l bg-card/30 lg:flex">
      {/* Header */}
      <div className="flex items-start justify-between gap-2 border-b p-4">
        <div className="min-w-0 flex-1 space-y-1">
          {isLoading ? (
            <>
              <Skeleton className="h-5 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
            </>
          ) : (
            <>
              <h2 className="line-clamp-2 text-base font-semibold leading-tight">
                {localized?.title}
              </h2>
              <p className="text-xs text-muted-foreground">
                {item?.sourceName} · {item && formatRelativeTime(item.publishedAt, t)}
              </p>
            </>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {/* Star / archive toggles — the write path behind the inbox
              status filters. Statuses are one-dimensional (unread /
              read / starred / archived), matching the sidecar model:
              toggling off returns the item to "read". */}
          {item && (
            <>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                disabled={statusMut.isPending}
                onClick={() =>
                  statusMut.mutate({
                    id: item.id,
                    status: item.status === "starred" ? "read" : "starred",
                  })
                }
                aria-label={item.status === "starred" ? t("detail.unstar") : t("detail.star")}
                title={item.status === "starred" ? t("detail.unstar") : t("detail.star")}
                data-testid="detail-star-toggle"
              >
                <Star
                  className={cn(
                    "h-4 w-4",
                    item.status === "starred" && "fill-amber-400 text-amber-400",
                  )}
                />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                disabled={statusMut.isPending}
                onClick={() =>
                  statusMut.mutate({
                    id: item.id,
                    status: item.status === "archived" ? "read" : "archived",
                  })
                }
                aria-label={item.status === "archived" ? t("detail.unarchive") : t("detail.archive")}
                title={item.status === "archived" ? t("detail.unarchive") : t("detail.archive")}
                data-testid="detail-archive-toggle"
              >
                <Archive
                  className={cn("h-4 w-4", item.status === "archived" && "text-primary")}
                />
              </Button>
            </>
          )}
          {/* Bilingual toggle — only render when both languages exist. */}
          {(hasEn && hasZh) && (
            <div
              role="group"
              aria-label="Language"
              className="inline-flex rounded-md border border-input bg-background p-0.5"
              data-testid="lang-toggle"
            >
              <LangPill
                label={t("detail.showEn")}
                active={activeLang === "en"}
                onClick={() => setOverrideLang("en")}
              />
              <LangPill
                label={t("detail.showZh")}
                active={activeLang === "zh"}
                onClick={() => setOverrideLang("zh")}
              />
            </div>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0"
            onClick={() => setSelectedItem(null)}
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-5 p-4">
          {isLoading ? (
            <>
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-24 w-full" />
            </>
          ) : item ? (
            <>
              {/* Bilibili video — embedded player (v0.2c).
                  We detect a B station item two ways:
                    1. `item.metadataJson.bvid` is set (preferred —
                       the BilibiliFetcher stamps it into the item's
                       metadata). This is the forward path and lets
                       us embed the official player iframe.
                    2. `item.url` contains "bilibili.com" — covers
                       legacy items and canonical watch-page URLs.
                  When the item is B station but the bvid is missing,
                  we fall back to a "open on Bilibili" link so the
                  user can still watch it; the iframe can't render
                  without a BV id. */}
              {isBilibiliItem(item) && (
                <BilibiliPlayer
                  bvid={itemBvid(item)}
                  title={localized?.title ?? item.title}
                />
              )}

              {/* YouTube video — embedded player (v0.2c). Same
                  detection pattern as Bilibili: prefer the fetcher-
                  stamped `metadataJson.video_id`, fall back to URL
                  parsing for robustness. */}
              {isYouTubeItem(item) && (
                <YouTubePlayer
                  videoId={itemYouTubeId(item)}
                  title={localized?.title ?? item.title}
                />
              )}

              {/* Summary — rendered as 1+ paragraphs (the LLM
                  sometimes breaks with blank lines) and with
                  **bold** markers promoted to <strong> via the
                  parseInline helper. */}
              {localized?.summary && (
                <section>
                  <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {t("detail.summary")}
                  </h3>
                  <div className="space-y-2 text-sm leading-relaxed">
                    {splitParagraphs(localized.summary).map((para, i) => (
                      <p key={i}>
                        {parseInline(para).map((node, j) =>
                          node.kind === "strong" ? (
                            <strong key={j} className="font-semibold text-foreground">
                              {node.text}
                            </strong>
                          ) : (
                            <span key={j} className="whitespace-pre-wrap">
                              {node.text}
                            </span>
                          ),
                        )}
                      </p>
                    ))}
                  </div>
                </section>
              )}

              {/* Key points — numbered so the reader can
                  reference point #3 instead of "the thing about
                  the kernel patch". The numbered chip replaces
                  the small bullet we had pre-v0.2b; it's louder
                  but the list was already structured, so a
                  numbered chip doesn't compete for attention
                  with anything else on the page. */}
              {localized && localized.keyPoints.length > 0 && (
                <section>
                  <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {t("detail.keyPoints")}
                  </h3>
                  <ol className="space-y-2 text-sm">
                    {localized.keyPoints.map((p, i) => (
                      <li key={i} className="flex gap-2.5">
                        <span
                          aria-hidden
                          className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold tabular-nums text-primary"
                        >
                          {i + 1}
                        </span>
                        <span className="leading-relaxed">
                          {parseInline(p).map((node, j) =>
                            node.kind === "strong" ? (
                              <strong key={j} className="font-semibold text-foreground">
                                {node.text}
                              </strong>
                            ) : (
                              <span key={j} className="whitespace-pre-wrap">
                                {node.text}
                              </span>
                            ),
                          )}
                        </span>
                      </li>
                    ))}
                  </ol>
                </section>
              )}

              {/* Tags — hash-prefixed chips (the social-tag
                  convention). Replaces the plain secondary Badge
                  from v0.2a: the leading # makes them feel
                  clickable / categorisable even though we don't
                  yet have a tag-click → filter action. When we
                  add that, the affordance will already be in
                  place. */}
              {localized && localized.tags.length > 0 && (
                <section>
                  <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {t("detail.tags")}
                  </h3>
                  <div className="flex flex-wrap gap-1.5">
                    {localized.tags.map((tag) => (
                      <Badge
                        key={tag}
                        variant="secondary"
                        className="gap-0.5 px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground"
                      >
                        <Hash className="h-2.5 w-2.5" />
                        {tag}
                      </Badge>
                    ))}
                  </div>
                </section>
              )}

              {/* User tags — editable, distinct from the auto tags above.
                  Always shown (even with zero tags) so the add affordance
                  is available. */}
              <section data-testid="user-tags">
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {t("detail.myTags")}
                </h3>
                <div className="flex flex-wrap items-center gap-1.5">
                  {(item.userTags ?? []).map((tag) => (
                    <Badge
                      key={tag}
                      variant="outline"
                      className="gap-1 px-1.5 py-0.5 text-[10px] font-normal"
                      data-testid={`user-tag-${tag}`}
                    >
                      #{tag}
                      <button
                        type="button"
                        aria-label={t("detail.removeTag", { tag })}
                        title={t("detail.removeTag", { tag })}
                        disabled={removeTagMut.isPending}
                        onClick={() => removeTagMut.mutate({ id: item.id, tag })}
                        className="rounded-sm text-muted-foreground hover:text-destructive"
                      >
                        <X className="h-2.5 w-2.5" />
                      </button>
                    </Badge>
                  ))}
                  <input
                    value={tagInput}
                    onChange={(e) => setTagInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        submitTag(item.id);
                      }
                    }}
                    placeholder={t("detail.addTagPlaceholder")}
                    maxLength={50}
                    data-testid="add-tag-input"
                    className="h-6 min-w-[6rem] flex-1 rounded border border-input bg-background px-1.5 text-[11px] placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  />
                </div>
              </section>

              {/* Pending-distillation hint */}
              {!item.distilledAt && (
                <section className="rounded-md border border-dashed border-muted-foreground/30 bg-muted/40 p-3 text-xs text-muted-foreground">
                  {t("inbox.pendingDistill")}
                </section>
              )}

              {/* Meta */}
              <section>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {t("detail.metadata")}
                </h3>
                <dl className="space-y-1 text-xs">
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">{t("detail.type")}</dt>
                    <dd className="capitalize">{item.contentType}</dd>
                  </div>
                  {item.author && (
                    <div className="flex justify-between">
                      <dt className="text-muted-foreground">{t("detail.author")}</dt>
                      <dd className="truncate max-w-[200px]">{item.author}</dd>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">{t("detail.status")}</dt>
                    <dd className="capitalize">{t(`itemStatus.${item.status as "unread" | "read" | "starred" | "archived"}`)}</dd>
                  </div>
                </dl>
              </section>

              {hasBothLanguages && null}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">{t("detail.notFound")}</p>
          )}
        </div>
      </ScrollArea>

      {/* Footer */}
      {item && (
        <div className="space-y-2 border-t p-3">
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-accent hover:text-accent-foreground"
            data-testid="detail-open-original"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            {t("detail.openOriginal")}
          </a>
          {isBilibiliItem(item) && (
            <a
              href={item.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-md border border-pink-500/40 bg-pink-500/10 px-3 text-xs font-medium text-pink-700 hover:bg-pink-500/20 dark:text-pink-300"
              data-testid="detail-open-bilibili"
            >
              <PlayCircle className="h-3.5 w-3.5" />
              {t("inbox.openOnBilibili")}
            </a>
          )}
          {isYouTubeItem(item) && (
            <a
              href={item.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-md border border-red-500/40 bg-red-500/10 px-3 text-xs font-medium text-red-700 hover:bg-red-500/20 dark:text-red-300"
              data-testid="detail-open-youtube"
            >
              <PlayCircle className="h-3.5 w-3.5" />
              {t("inbox.openOnYoutube")}
            </a>
          )}
        </div>
      )}
    </aside>
  );
}

function LangPill({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors",
        active
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:text-foreground",
      )}
      aria-pressed={active}
      data-active={active}
    >
      {label}
    </button>
  );
}

// ----- Bilibili helpers & player ------------------------------------------

/**
 * The BV id for a bilibili item. The authoritative copy lives in
 * `metadataJson.bvid` (the BilibiliFetcher stamps it there); the URL
 * parse is a safety net for legacy rows. (The old top-level `item.bvid`
 * field never existed in the sidecar's response — it was always
 * undefined, so the URL fallback was silently doing all the work.)
 */
function itemBvid(item: KnowledgeItem): string | undefined {
  const fromMeta = item.metadataJson?.bvid;
  if (typeof fromMeta === "string" && fromMeta) return fromMeta;
  return extractBvidFromUrl(item.url);
}

/**
 * Heuristic: an item is "B station" if the fetcher stamped a `bvid`
 * into its metadata OR the URL points at bilibili.com (safety net
 * for legacy rows).
 */
function isBilibiliItem(item: KnowledgeItem): boolean {
  if (typeof item.metadataJson?.bvid === "string" && item.metadataJson.bvid) return true;
  return /bilibili\.com/i.test(item.url ?? "");
}

/**
 * Pull a BV id out of a bilibili.com URL. Returns undefined when
 * no BV id is found (the URL might be a mid page or a search
 * page — in that case the iframe can't render anyway, so the
 * detail panel degrades to the "open on Bilibili" link).
 */
function extractBvidFromUrl(url: string): string | undefined {
  const m = url?.match(/BV[0-9A-Za-z]{8,}/i);
  return m ? m[0] : undefined;
}

/**
 * Embed the official Bilibili player iframe. The player URL is
 * `https://player.bilibili.com/player.html?bvid=<id>&autoplay=0`
 * — autoplay is off because the user already chose this item by
 * clicking it; auto-playing audio would be obnoxious in a panel
 * next to the inbox list.
 *
 * When `bvid` is missing we render a fallback card explaining
 * why the embed is absent and offering the "open on Bilibili"
 * link in the footer as a substitute.
 */
// ----- YouTube helpers & player --------------------------------------------

/**
 * The YouTube video id for an item. Authoritative copy is
 * `metadataJson.video_id` (the YouTubeFetcher stamps it there);
 * URL parsing is the safety net, same policy as `itemBvid`.
 */
function itemYouTubeId(item: KnowledgeItem): string | undefined {
  const fromMeta = item.metadataJson?.video_id;
  if (typeof fromMeta === "string" && fromMeta) return fromMeta;
  const m = item.url?.match(/(?:[?&]v=|youtu\.be\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : undefined;
}

function isYouTubeItem(item: KnowledgeItem): boolean {
  const feedKind = item.metadataJson?.feed_kind;
  if (typeof feedKind === "string" && feedKind === "youtube") return true;
  return /youtube\.com\/watch|youtu\.be\//i.test(item.url ?? "");
}

/**
 * Embed the privacy-enhanced YouTube player (youtube-nocookie.com).
 * autoplay stays off for the same reason as the Bilibili player.
 * Missing video id → fallback card; the footer "open on YouTube"
 * link is the substitute.
 */
function YouTubePlayer({ videoId, title }: { videoId?: string; title: string }) {
  const { t } = useLanguage();
  if (!videoId) {
    return (
      <section
        className="rounded-md border border-dashed border-red-500/30 bg-red-500/5 p-3 text-xs text-red-700 dark:text-red-300"
        data-testid="detail-youtube-missing"
      >
        {t("inbox.youtubeSourceMissing")}
      </section>
    );
  }
  const src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}`;
  return (
    <section className="space-y-1.5" data-testid="detail-youtube-player">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {t("inbox.youtubePlayer")}
      </h3>
      <div className="relative aspect-video w-full overflow-hidden rounded-md border border-red-500/30 bg-black">
        <iframe
          src={src}
          title={`YouTube player — ${title}`}
          allowFullScreen
          frameBorder="0"
          allow="encrypted-media; picture-in-picture"
          sandbox="allow-same-origin allow-scripts allow-popups allow-presentation"
          className="absolute inset-0 h-full w-full"
        />
      </div>
    </section>
  );
}

function BilibiliPlayer({ bvid, title }: { bvid?: string; title: string }) {
  const { t } = useLanguage();
  if (!bvid) {
    return (
      <section
        className="rounded-md border border-dashed border-pink-500/30 bg-pink-500/5 p-3 text-xs text-pink-700 dark:text-pink-300"
        data-testid="detail-bilibili-missing"
      >
        {t("inbox.bilibiliSourceMissing")}
      </section>
    );
  }
  const src = `https://player.bilibili.com/player.html?bvid=${encodeURIComponent(bvid)}&autoplay=0`;
  return (
    <section
      className="space-y-1.5"
      data-testid="detail-bilibili-player"
    >
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {t("inbox.bilibiliPlayer")}
      </h3>
      <div className="relative aspect-video w-full overflow-hidden rounded-md border border-pink-500/30 bg-black">
        <iframe
          src={src}
          title={`Bilibili player — ${title}`}
          allowFullScreen
          scrolling="no"
          frameBorder="0"
          sandbox="allow-top-navigation allow-same-origin allow-forms allow-scripts allow-popups"
          className="absolute inset-0 h-full w-full"
        />
      </div>
    </section>
  );
}
