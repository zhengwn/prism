import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Library, Search } from "lucide-react";
import { usePrismStore } from "@/store";
import { useMemo } from "react";
import { useLanguage } from "@/hooks/useLanguage";
import type { KnowledgeItem } from "@/types";

function pickTitle(item: KnowledgeItem, preferEn: boolean): string {
  if (preferEn) return item.titleEn || item.title;
  return item.titleZh || item.titleEn || item.title;
}

function pickSummary(item: KnowledgeItem, preferEn: boolean): string | undefined {
  if (preferEn) return item.summaryEn ?? item.summary;
  return item.summaryZh ?? item.summaryEn ?? item.summary;
}

function pickTags(item: KnowledgeItem): string[] {
  // Tags always surface in Chinese — the distiller is the source of truth
  // for tag translation.
  return item.tagsZh ?? item.tags ?? [];
}

export function KnowledgePage() {
  const { data: items, isLoading } = useQuery({ queryKey: ["items"], queryFn: () => api.listItems() });
  const searchQuery = usePrismStore((s) => s.searchQuery);
  const { t, language } = useLanguage();
  const preferEn = language === "en";

  const filtered = useMemo(() => {
    if (!items) return [];
    if (!searchQuery) return items;
    const q = searchQuery.toLowerCase();
    return items.filter((it) => {
      const hay = [
        it.titleEn,
        it.titleZh ?? "",
        it.summaryEn ?? "",
        it.summaryZh ?? "",
        (it.tagsZh ?? []).join(" "),
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [items, searchQuery]);

  const grouped = useMemo(() => {
    const map = new Map<string, KnowledgeItem[]>();
    filtered.forEach((it) => {
      const key = it.sourceName;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(it);
    });
    return Array.from(map.entries());
  }, [filtered]);

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">{t("knowledge.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("knowledge.description")}</p>
        </div>
        <Badge variant="secondary" className="gap-1">
          <Library className="h-3 w-3" />
          {t("knowledge.entries", { count: items?.length ?? 0 })}
        </Badge>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : grouped.length === 0 ? (
        <div className="flex h-64 items-center justify-center rounded-lg border border-dashed">
          <div className="text-center space-y-2">
            <Search className="mx-auto h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {searchQuery ? t("knowledge.noMatches") : t("knowledge.noKnowledge")}
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          {grouped.map(([sourceName, list]) => (
            <section key={sourceName}>
              <h3 className="mb-2 text-sm font-semibold">{sourceName}</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {list.map((item) => {
                  const tags = pickTags(item);
                  const title = pickTitle(item, preferEn);
                  const summary = pickSummary(item, preferEn);
                  return (
                    <Card key={item.id} className="hover:bg-accent/30">
                      <CardHeader className="pb-2">
                        <CardTitle className="line-clamp-2 text-sm">{title}</CardTitle>
                        {tags.length > 0 && (
                          <CardDescription className="text-xs">
                            {tags.slice(0, 3).join(" · ")}
                          </CardDescription>
                        )}
                      </CardHeader>
                      <CardContent className="pt-0">
                        {summary ? (
                          <p className="line-clamp-3 text-xs text-muted-foreground">{summary}</p>
                        ) : (
                          <p className="text-xs italic text-muted-foreground">
                            {t("inbox.pendingDistill")}
                          </p>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
