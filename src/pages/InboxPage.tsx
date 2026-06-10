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
import { Inbox as InboxIcon, Star, CheckCircle2, RefreshCw, AlertCircle, Check, Sparkles } from "lucide-react";
import type { DistillProgress, KnowledgeItem } from "@/types";
import { useLanguage } from "@/hooks/useLanguage";
import { useDistillProgress } from "@/hooks/useDistillProgress";

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
  // The search input is now backed by the sidecar's FTS5 index
  // (see python/prism_sidecar/fts5.py). We pass the query down to
  // the API rather than filtering client-side, so the inbox
  // stays responsive even when there are thousands of items.
  //
  // Debounce: the input still updates the Zustand store on every
  // keystroke (so the search box always reflects what the user
  // typed), but we only fire the network query once the user has
  // stopped typing for 200ms. Without this, fast typing creates
  // a queue of in-flight requests that all resolve out of order
  // and the list flickers. The 200ms is small enough to feel
  // instant but long enough to coalesce most typing bursts.
  const searchQuery = usePrismStore((s) => s.searchQuery);
  const [debouncedQuery, setDebouncedQuery] = useState(searchQuery);
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebouncedQuery(searchQuery);
    }, 200);
    return () => window.clearTimeout(handle);
  }, [searchQuery]);

  const { data: items, isLoading } = useQuery({
    queryKey: ["items", { q: debouncedQuery }],
    queryFn: () => api.listItems({ q: debouncedQuery || undefined }),
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
  const statusFilter = usePrismStore((s) => s.statusFilter);
  const setStatusFilter = usePrismStore((s) => s.setStatusFilter);
  const { t, language } = useLanguage();
  const preferEn = language === "en";

  // Sync button state. `idle` is the default. While a sync is running we
  // lock the button. After it finishes we briefly show "success" or
  // "error" so the user gets feedback, then fall back to idle after 2s.
  const [syncState, setSyncState] = useState<SyncUiState>("idle");
  const [toast, setToast] = useState<{ kind: "success" | "error" | "info"; text: string } | null>(null);
  const resetTimer = useRef<number | null>(null);
  // The job_id of the in-flight sync, captured when the POST
  // /api/sync call returns. Needed by handleCancel to call
  // POST /api/sync/{jobId}/cancel. Null when no sync is running.
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  // Live distill progress — drives the progress bar under the inbox
  // header. We use this for both the determinate "X / Y" state
  // (redistill, where we know `pending` up front) and the
  // indeterminate spinner (sync, where `pending` is 0 until each
  // source is fetched).
  const distill = useDistillProgress();

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
      // v0.2b: /api/sync returns immediately with status=running
      // and the pipeline runs in the background. We poll
      // /api/sync/{jobId} until it finishes (every 250ms) so the
      // UI can flip to success/error/cancelled as soon as the
      // job settles. Polling — not a one-shot await — is the
      // right pattern here because the job can take anywhere
      // from 1s (no sources, no items) to 30s+ (slow fetcher,
      // many items, slow LLM).
      const initial = await api.syncAll();
      const jobId = initial.jobId;
      setActiveJobId(jobId);

      const POLL_MS = 250;
      const POLL_TIMEOUT_MS = 5 * 60 * 1000; // 5 min — anything longer = bug
      const deadline = Date.now() + POLL_TIMEOUT_MS;
      let final = initial;
      while (final.status === "running" && Date.now() < deadline) {
        await new Promise<void>((r) => window.setTimeout(r, POLL_MS));
        final = await api.getSyncStatus(jobId);
      }

      // Now flip the button state to match the final status.
      // 'done' and 'cancelled' both count as "not an error" —
      // the user cancelled deliberately, not because the
      // pipeline broke.
      if (final.status === "cancelled") {
        setToast({
          kind: "info",
          text: t("inbox.syncResultCancelled", {
            distilled: final.itemsDistilled,
            total: final.sourcesTotal,
          }),
        });
      } else if (final.status === "error") {
        setSyncState("error");
        setToast({ kind: "error", text: t("inbox.syncError") });
        qc.invalidateQueries({ queryKey: ["items"] });
        qc.invalidateQueries({ queryKey: ["sources"] });
        return;
      } else {
        setToast({
          kind: "success",
          text: t("inbox.syncResult", {
            new: final.itemsNew,
            distilled: final.itemsDistilled,
          }),
        });
      }
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
      setSyncState("success");
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
        setActiveJobId(null);
      }, 2_500);
    }
  };

  // Fire-and-forget cancel: the sidecar sets a flag and the
  // pipeline picks it up at the next source boundary. The
  // /api/sync POST that started the run is still blocked on
  // the network — it will return with status="cancelled" once
  // the flag is observed. We don't try to "speed up" the
  // cancel by closing the connection; the user just sees
  // "Sync cancelled" once the in-flight source finishes.
  const handleCancel = async () => {
    if (!activeJobId) return;
    try {
      await api.cancelSync(activeJobId);
    } catch (e) {
      console.error("[prism] cancel failed:", e);
    }
  };

  // Client-side filters: source / status. The text search is now
  // server-side via FTS5 (see useQuery above); everything else
  // stays here because it's cheap and changes immediately when
  // the user toggles a filter, whereas a server roundtrip would
  // flash a loading state for half a second.
  const filteredItems: KnowledgeItem[] = (items ?? []).filter((it) => {
    if (selectedSourceId && it.sourceId !== selectedSourceId) return false;
    if (statusFilter === "unread" && it.status !== "unread") return false;
    if (statusFilter === "starred" && it.status !== "starred") return false;
    if (statusFilter === "archived" && it.status !== "archived") return false;
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
      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="flex h-12 items-center justify-between border-b px-4">
          <div>
            <h2 className="text-sm font-semibold">{t("inbox.title")}</h2>
            <p className="text-xs text-muted-foreground">
              {t("inbox.count", { count: filteredItems.length })}
              {selectedSourceId ? t("inbox.inSelectedSource") : ""}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <SyncButton
              state={syncState}
              onClick={syncState === "running" ? handleCancel : handleSync}
            />
            {toast && (
              <span
                className={cn(
                  "inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px]",
                  toast.kind === "success"
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300"
                    : toast.kind === "info"
                      ? "border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-300"
                      : "border-destructive/40 bg-destructive/10 text-destructive",
                )}
                role="status"
                aria-live="polite"
                data-testid="sync-toast"
              >
                {toast.kind === "success" ? (
                  <Check className="h-3 w-3" />
                ) : toast.kind === "info" ? (
                  <AlertCircle className="h-3 w-3" />
                ) : (
                  <AlertCircle className="h-3 w-3" />
                )}
                {toast.text}
              </span>
            )}
          </div>
        </div>

        <DistillProgressBar progress={distill} />

        <ScrollArea className="flex-1">
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
  // v0.2b: when the sync is running, this same button acts as
  // "Cancel" instead of being disabled. The parent wires the
  // click to handleCancel in that case, so the user can stop
  // a long-running sync without waiting for it to finish.
  const isCancel = state === "running";
  const label = isCancel
    ? t("inbox.syncCancel")
    : state === "success"
      ? t("inbox.syncSuccess")
      : state === "error"
        ? t("inbox.syncError")
        : t("inbox.syncNow");
  const Icon = isCancel
    ? AlertCircle
    : state === "success"
      ? Check
      : state === "error"
        ? AlertCircle
        : RefreshCw;
  return (
    <Button
      size="sm"
      variant={
        isCancel
          ? "outline"
          : state === "error"
            ? "destructive"
            : state === "success"
              ? "secondary"
              : "default"
      }
      onClick={onClick}
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

/**
 * Live distill progress strip — sits between the inbox header and
 * the item list, animates while a run is in flight, and shows a
 * one-shot success/fail summary when the run ends.
 *
 * Why a strip (not a modal/toast)
 * --------------------------------
 * The user explicitly asked "where did the distilled items go?"
 * — a transient toast can't answer that. The strip is always in
 * place and shows the running state with the current item title;
 * once the run ends, we leave the summary up for ~6s so the user
 * can read "X distilled, Y failed" and then auto-hide.
 *
 * "Distilled items land here" hint
 * ---------------------------------
 * Below the strip we render a one-line explainer that connects
 * the abstract counter to the concrete list below ("items with
 * the Pending badge will turn into Chinese titles here"). This is
 * the most direct fix for "I don't know where the results went".
 */
function DistillProgressBar({ progress }: { progress: DistillProgress }) {
  const { t } = useLanguage();

  // Three states:
  //   - running  : show bar + "正在蒸馏 X / Y — <current title>"
  //   - finished : show a summary line for 6s, then auto-hide
  //   - idle     : show the "where to find" hint line so first-time
  //                users understand what the Pending badge means
  type UiState = "idle" | "running" | "summary";
  const [uiState, setUiState] = useState<UiState>("idle");
  const [summary, setSummary] = useState<{
    kind: "ok" | "fail";
    text: string;
  } | null>(null);
  const summaryTimer = useRef<number | null>(null);

  // Drive the strip's state machine from the live progress.
  useEffect(() => {
    if (progress.isRunning) {
      setUiState("running");
      setSummary(null);
      return;
    }
    // Just ended. If anything happened, show a one-shot summary.
    const total = progress.distilled + progress.failed;
    if (total > 0) {
      const text = progress.failed > 0
        ? t("inbox.distillProgressDone", {
            distilled: progress.distilled,
            failed: progress.failed,
          })
        : t("inbox.distillProgressDoneNoFail", {
            distilled: progress.distilled,
          });
      setSummary({
        kind: progress.failed > 0 ? "fail" : "ok",
        text: progress.lastError
          ? t("inbox.distillProgressError", { error: progress.lastError })
          : text,
      });
      setUiState("summary");
      if (summaryTimer.current !== null) window.clearTimeout(summaryTimer.current);
      summaryTimer.current = window.setTimeout(() => {
        setUiState("idle");
        setSummary(null);
      }, 6_000);
    } else {
      setUiState("idle");
      setSummary(null);
    }
  }, [progress.isRunning, progress.distilled, progress.failed, progress.lastError, t]);

  useEffect(() => {
    return () => {
      if (summaryTimer.current !== null) window.clearTimeout(summaryTimer.current);
    };
  }, []);

  if (uiState === "running") {
    const isDeterminate = progress.pending > 0;
    const percent = isDeterminate
      ? Math.min(100, Math.round((progress.distilled / progress.pending) * 100))
      : 0;
    return (
      <div
        data-testid="distill-progress-bar"
        data-state="running"
        className="border-b bg-primary/5 px-4 py-2 text-xs"
      >
        <div className="mb-1.5 flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 animate-pulse text-primary" />
          <span className="font-medium text-foreground">
            {isDeterminate
              ? t("inbox.distillProgressRunning", {
                  distilled: progress.distilled,
                  pending: progress.pending,
                })
              : t("inbox.distillProgressRunningIndeterminate", {
                  distilled: progress.distilled,
                })}
          </span>
          {progress.currentTitle && (
            <span className="truncate text-muted-foreground">
              {t("inbox.distillProgressCurrent", { title: progress.currentTitle })}
            </span>
          )}
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              "h-full rounded-full bg-primary transition-all duration-200",
              isDeterminate ? "" : "animate-pulse",
            )}
            style={isDeterminate ? { width: `${percent}%` } : { width: "30%" }}
          />
        </div>
      </div>
    );
  }

  if (uiState === "summary" && summary) {
    return (
      <div
        data-testid="distill-progress-bar"
        data-state="summary"
        className={cn(
          "border-b px-4 py-2 text-xs",
          summary.kind === "ok"
            ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
            : "bg-destructive/10 text-destructive",
        )}
      >
        <div className="flex items-center gap-2">
          {summary.kind === "ok" ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <AlertCircle className="h-3.5 w-3.5" />
          )}
          <span className="font-medium">{summary.text}</span>
        </div>
      </div>
    );
  }

  // Idle: always show the "where to find" hint so first-time users
  // understand what the Pending badge means.
  return (
    <div
      data-testid="distill-progress-bar"
      data-state="idle"
      className="border-b bg-muted/30 px-4 py-1.5 text-[11px] text-muted-foreground"
    >
      {t("inbox.distillProgressWhereToFind")}
    </div>
  );
}
