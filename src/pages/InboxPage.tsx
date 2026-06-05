import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { usePrismStore } from "@/store";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { formatRelativeTime, cn } from "@/lib/utils";
import { Inbox as InboxIcon, Star, CheckCircle2, RefreshCw, AlertCircle, Check } from "lucide-react";
import type { KnowledgeItem } from "@/types";
import { useLanguage } from "@/hooks/useLanguage";

type SyncUiState = "idle" | "running" | "success" | "error";

/**
 * Pick the display title for an item. Bilingual preference follows the
 * current UI language — Chinese UI gets `titleZh` when available,
 * otherwise `titleEn`. DetailPanel has its own explicit EN/中 toggle
 * for users who want to peek at the other language.
 */
function displayTitle(item: KnowledgeItem, preferEn: boolean): string {
  if (preferEn) return item.titleEn || item.title;
  return item.titleZh || item.titleEn || item.title;
}

function displaySummary(item: KnowledgeItem, preferEn: boolean): string | undefined {
  if (preferEn) return item.summaryEn || item.summary;
  return item.summaryZh || item.summaryEn || item.summary;
}

export function InboxPage() {
  const { data: items, isLoading } = useQuery({
    queryKey: ["items"],
    queryFn: () => api.listItems(),
  });

  const { data: sources } = useQuery({
    queryKey: ["sources"],
    queryFn: () => api.listSources(),
  });

  const qc = useQueryClient();
  const selectedSourceId = usePrismStore((s) => s.selectedSourceId);
  const setSelectedSource = usePrismStore((s) => s.setSelectedSource);
  const selectedItemId = usePrismStore((s) => s.selectedItemId);
  const setSelectedItem = usePrismStore((s) => s.setSelectedItem);
  const searchQuery = usePrismStore((s) => s.searchQuery);
  const statusFilter = usePrismStore((s) => s.statusFilter);
  const setStatusFilter = usePrismStore((s) => s.setStatusFilter);
  const { t, language } = useLanguage();
  const preferEn = language === "en";

  // Sync button state. `idle` is the default. While a sync is running we
  // lock the button. After it finishes we briefly show "success" or
  // "error" so the user gets feedback, then fall back to idle after 2s.
  const [syncState, setSyncState] = useState<SyncUiState>("idle");
  const [toast, setToast] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const resetTimer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (resetTimer.current !== null) {
        window.clearTimeout(resetTimer.current);
      }
    };
  }, []);

  const handleSync = async () => {
    if (syncState === "running") return;
    setSyncState("running");
    setToast(null);
    try {
      const result = await api.syncAll();
      setSyncState("success");
      setToast({
        kind: "success",
        text: t("inbox.syncResult", { new: result.itemsNew, distilled: result.itemsDistilled }),
      });
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
    } catch (e) {
      console.error("[prism] sync failed:", e);
      setSyncState("error");
      setToast({ kind: "error", text: t("inbox.syncError") });
    } finally {
      // After a brief feedback window, drop back to idle. This lets the
      // success/error state be visible without persisting forever.
      if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
      resetTimer.current = window.setTimeout(() => {
        setSyncState("idle");
        setToast(null);
      }, 2_500);
    }
  };

  const filteredItems: KnowledgeItem[] = (items ?? []).filter((it) => {
    if (selectedSourceId && it.sourceId !== selectedSourceId) return false;
    if (statusFilter === "unread" && it.status !== "unread") return false;
    if (statusFilter === "starred" && it.status !== "starred") return false;
    if (statusFilter === "archived" && it.status !== "archived") return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const haystack = [
        it.titleEn,
        it.titleZh ?? "",
        it.summaryEn ?? "",
        it.summaryZh ?? "",
        (it.tagsZh ?? []).join(" "),
      ]
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });

  return (
    <div className="flex h-full">
      {/* Source filter rail */}
      <div className="hidden w-48 shrink-0 border-r bg-card/20 p-3 md:block">
        <h3 className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t("inbox.filter")}
        </h3>
        <Button
          variant={selectedSourceId === null ? "secondary" : "ghost"}
          size="sm"
          className="w-full justify-start"
          onClick={() => setSelectedSource(null)}
        >
          {t("inbox.allSources")}
        </Button>

        <h3 className="mt-4 mb-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t("inbox.status")}
        </h3>
        <div className="space-y-1">
          {(["all", "unread", "starred", "archived"] as const).map((s) => (
            <Button
              key={s}
              variant={statusFilter === s ? "secondary" : "ghost"}
              size="sm"
              className="w-full justify-start"
              onClick={() => setStatusFilter(s)}
            >
              {t(`statusFilter.${s}`)}
            </Button>
          ))}
        </div>

        <h3 className="mt-4 mb-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t("inbox.sources")}
        </h3>
        <div className="space-y-1">
          {sources?.map((src) => (
            <Button
              key={src.id}
              variant={selectedSourceId === src.id ? "secondary" : "ghost"}
              size="sm"
              className="w-full justify-between"
              onClick={() => setSelectedSource(src.id)}
            >
              <span className="truncate">{src.name}</span>
              <span className="text-[10px] text-muted-foreground">{src.itemCount}</span>
            </Button>
          ))}
        </div>
      </div>

      {/* Items list */}
      <div className="flex-1 overflow-hidden">
        <div className="flex h-12 items-center justify-between border-b px-4">
          <div>
            <h2 className="text-sm font-semibold">{t("inbox.title")}</h2>
            <p className="text-xs text-muted-foreground">
              {t("inbox.count", { count: filteredItems.length })}
              {selectedSourceId ? t("inbox.inSelectedSource") : ""}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <SyncButton state={syncState} onClick={handleSync} />
            {toast && (
              <span
                className={cn(
                  "inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px]",
                  toast.kind === "success"
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300"
                    : "border-destructive/40 bg-destructive/10 text-destructive",
                )}
                role="status"
                aria-live="polite"
                data-testid="sync-toast"
              >
                {toast.kind === "success" ? (
                  <Check className="h-3 w-3" />
                ) : (
                  <AlertCircle className="h-3 w-3" />
                )}
                {toast.text}
              </span>
            )}
          </div>
        </div>

        <ScrollArea className="h-[calc(100%-3rem)]">
          {isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-24 w-full" />
              ))}
            </div>
          ) : filteredItems.length === 0 ? (
            <EmptyState />
          ) : (
            <div className="space-y-2 p-4">
              {filteredItems.map((item) => (
                <ItemRow
                  key={item.id}
                  item={item}
                  selected={item.id === selectedItemId}
                  onClick={() => setSelectedItem(item.id)}
                  preferEn={preferEn}
                />
              ))}
            </div>
          )}
        </ScrollArea>
      </div>
    </div>
  );
}

function SyncButton({ state, onClick }: { state: SyncUiState; onClick: () => void }) {
  const { t } = useLanguage();
  const label =
    state === "running"
      ? t("inbox.syncing")
      : state === "success"
        ? t("inbox.syncSuccess")
        : state === "error"
          ? t("inbox.syncError")
          : t("inbox.syncNow");
  const Icon =
    state === "running"
      ? RefreshCw
      : state === "success"
        ? Check
        : state === "error"
          ? AlertCircle
          : RefreshCw;
  return (
    <Button
      size="sm"
      variant={state === "error" ? "destructive" : state === "success" ? "secondary" : "default"}
      onClick={onClick}
      disabled={state === "running"}
      className="gap-1.5"
      data-testid="sync-now-button"
      data-sync-state={state}
    >
      <Icon className={cn("h-3.5 w-3.5", state === "running" && "animate-spin")} />
      {label}
    </Button>
  );
}

function ItemRow({
  item,
  selected,
  onClick,
  preferEn,
}: {
  item: KnowledgeItem;
  selected: boolean;
  onClick: () => void;
  preferEn: boolean;
}) {
  const { t } = useLanguage();
  const summary = displaySummary(item, preferEn);
  const title = displayTitle(item, preferEn);
  return (
    <Card
      className={cn(
        "cursor-pointer transition-colors hover:bg-accent/40",
        selected && "border-primary/50 bg-accent/50",
      )}
      onClick={onClick}
    >
      <CardContent className="p-3">
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0 space-y-1">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
                {item.sourceName}
              </Badge>
              <span>·</span>
              <span>{formatRelativeTime(item.publishedAt)}</span>
              {item.status === "starred" && <Star className="h-3 w-3 fill-amber-400 text-amber-400" />}
              {item.status === "read" && <CheckCircle2 className="h-3 w-3 text-emerald-500" />}
              {!item.distilledAt && (
                <Badge variant="outline" className="h-5 px-1.5 text-[10px]">
                  {t("inbox.pendingDistill")}
                </Badge>
              )}
            </div>
            <h3 className="text-sm font-medium leading-tight">{title}</h3>
            {summary && <p className="line-clamp-2 text-xs text-muted-foreground">{summary}</p>}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function EmptyState() {
  const { t } = useLanguage();
  return (
    <div className="flex h-full items-center justify-center p-12">
      <div className="max-w-sm text-center space-y-3">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl prism-gradient">
          <InboxIcon className="h-6 w-6 text-white" />
        </div>
        <h3 className="text-sm font-semibold">{t("inbox.emptyTitle")}</h3>
        <p className="text-xs text-muted-foreground">{t("inbox.emptyDescription")}</p>
      </div>
    </div>
  );
}
