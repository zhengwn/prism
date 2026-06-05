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
};

const KIND_OPTIONS: SourceKind[] = ["rss", "blog", "youtube", "podcast", "x", "pdf", "file"];

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
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {t("sources.itemsCount", { count: source.itemCount })} · {t("sources.lastSynced")}{" "}
            {source.lastSyncedAt ? formatRelativeTime(source.lastSyncedAt) : t("sources.lastSyncedNever")}
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

  const createMut = useMutation({
    mutationFn: () => api.createSource({ name, kind, url, enabled: true }),
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
                placeholder={t("sources.addDialog.namePlaceholder")}
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
                placeholder={t("sources.addDialog.urlPlaceholder")}
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
