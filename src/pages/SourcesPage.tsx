import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Rss, Plus, Trash2, ExternalLink } from "lucide-react";
import type { SourceKind } from "@/types";
import { formatRelativeTime } from "@/lib/utils";
import { useLanguage } from "@/hooks/useLanguage";

const kindIcon: Record<SourceKind, string> = {
  rss: "📡",
  youtube: "▶️",
  podcast: "🎙️",
  blog: "📝",
  x: "✕",
  pdf: "📄",
  file: "📁",
};

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

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">{t("sources.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("sources.description")}</p>
        </div>
        <Button className="gap-1.5">
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
            <Card key={src.id}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-xl">{kindIcon[src.kind]}</span>
                    <div>
                      <CardTitle className="text-sm">{src.name}</CardTitle>
                      <CardDescription className="text-xs">
                        <a
                          href={src.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 hover:underline"
                        >
                          {new URL(src.url).hostname}
                          <ExternalLink className="h-2.5 w-2.5" />
                        </a>
                      </CardDescription>
                    </div>
                  </div>
                  <Badge variant={src.enabled ? "default" : "secondary"}>
                    {src.enabled ? t("sources.active") : t("sources.paused")}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>
                    {t("sources.itemsCount", { count: src.itemCount })} · {t("sources.lastSynced")}{" "}
                    {src.lastSyncedAt ? formatRelativeTime(src.lastSyncedAt) : t("sources.lastSyncedNever")}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-destructive"
                    onClick={() => deleteMut.mutate(src.id)}
                    disabled={deleteMut.isPending}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
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
