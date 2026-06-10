import { useState } from "react";
import { X, ExternalLink, Sparkles, Hash } from "lucide-react";
import { usePrismStore } from "@/store";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatRelativeTime, cn } from "@/lib/utils";
import { useLanguage } from "@/hooks/useLanguage";
import { parseInline, splitParagraphs } from "@/lib/inline-markdown";
import type { KnowledgeItem } from "@/types";

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

  const { data: item, isLoading } = useQuery({
    queryKey: ["item", selectedItemId],
    queryFn: () => api.getItem(selectedItemId!),
    enabled: !!selectedItemId,
  });

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
                {item?.sourceName} · {item && formatRelativeTime(item.publishedAt)}
              </p>
            </>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
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
        <div className="border-t p-3">
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-accent hover:text-accent-foreground"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            {t("detail.openOriginal")}
          </a>
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
