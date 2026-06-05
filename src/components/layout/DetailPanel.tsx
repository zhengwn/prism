import { X, ExternalLink, Sparkles } from "lucide-react";
import { usePrismStore } from "@/store";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatRelativeTime } from "@/lib/utils";
import { useLanguage } from "@/hooks/useLanguage";

export function DetailPanel() {
  const selectedItemId = usePrismStore((s) => s.selectedItemId);
  const setSelectedItem = usePrismStore((s) => s.setSelectedItem);
  const { t } = useLanguage();

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
                {item?.title}
              </h2>
              <p className="text-xs text-muted-foreground">
                {item?.sourceName} · {item && formatRelativeTime(item.publishedAt)}
              </p>
            </>
          )}
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0"
          onClick={() => setSelectedItem(null)}
        >
          <X className="h-4 w-4" />
        </Button>
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
              {/* Summary */}
              {item.summary && (
                <section>
                  <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {t("detail.summary")}
                  </h3>
                  <p className="text-sm leading-relaxed">{item.summary}</p>
                </section>
              )}

              {/* Key points */}
              {item.keyPoints && item.keyPoints.length > 0 && (
                <section>
                  <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {t("detail.keyPoints")}
                  </h3>
                  <ul className="space-y-1.5 text-sm">
                    {item.keyPoints.map((p, i) => (
                      <li key={i} className="flex gap-2">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-primary" />
                        <span>{p}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {/* Tags */}
              {item.tags && item.tags.length > 0 && (
                <section>
                  <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {t("detail.tags")}
                  </h3>
                  <div className="flex flex-wrap gap-1">
                    {item.tags.map((tag) => (
                      <Badge key={tag} variant="secondary">
                        {tag}
                      </Badge>
                    ))}
                  </div>
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
