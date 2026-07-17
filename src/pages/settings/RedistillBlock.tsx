import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { RefreshCw, Loader2, Check, AlertCircle } from "lucide-react";
import { useDistillProgress } from "@/hooks/useDistillProgress";
import { useLanguage } from "@/hooks/useLanguage";
import { cn } from "@/lib/utils";

export function RedistillBlock() {
  const { t } = useLanguage();
  const qc = useQueryClient();
  const { data: pending, refetch: refetchPending } = useQuery({
    queryKey: ["distillPending"],
    queryFn: () => api.getPendingDistillCount(),
    refetchInterval: 15_000,
  });
  const [feedback, setFeedback] = useState<
    | { kind: "success" | "error"; text: string }
    | { kind: "keyInvalid"; text: string }
    | null
  >(null);

  // v0.5.x: the batch runs in the sidecar as a BACKGROUND task — the
  // POST returns immediately with startedPending set. Live counters and
  // the final outcome come from the shared distill progress stream
  // (the same source the inbox progress bar renders).
  const progress = useDistillProgress();
  // Whether THIS block kicked off a run and is waiting for its end.
  // The progress store is shared with sync runs, so without the gate a
  // finishing sync would render a bogus "redistill result" here.
  const awaitingRun = useRef(false);
  // The end-of-run detector must have SEEN the run in a running state
  // first — right after the POST resolves, the latest snapshot may
  // still be a stale finished-run frame from earlier.
  const sawRunning = useRef(false);

  const redistillMut = useMutation({
    mutationFn: () => api.redistill(),
    onSuccess: (r) => {
      awaitingRun.current = true;
      sawRunning.current = false;
      setFeedback({
        kind: "success",
        text: t("settings.redistill.started", { started: r.startedPending }),
      });
    },
    onError: (e) => {
      console.error("[prism] redistill failed:", e);
      setFeedback({ kind: "error", text: t("inbox.syncError") });
    },
  });

  // Watch the shared progress stream for the end of OUR run, then
  // surface the real counters (and the key-invalid outcome, which now
  // arrives mid-run via the progress store's lastError).
  useEffect(() => {
    if (!awaitingRun.current) return;
    if (progress.isRunning) {
      sawRunning.current = true;
      return;
    }
    if (!sawRunning.current) return;
    awaitingRun.current = false;
    sawRunning.current = false;
    qc.invalidateQueries({ queryKey: ["items"] });
    qc.invalidateQueries({ queryKey: ["distillPending"] });
    void refetchPending();
    if (progress.lastError?.startsWith("key_invalid")) {
      setFeedback({ kind: "keyInvalid", text: progress.lastError });
    } else {
      setFeedback({
        kind: progress.failed > 0 ? "error" : "success",
        text: t("settings.redistill.result", {
          distilled: progress.distilled,
          failed: progress.failed,
          started: progress.pending,
        }),
      });
    }
  }, [
    progress.isRunning,
    progress.lastError,
    progress.distilled,
    progress.failed,
    progress.pending,
    qc,
    refetchPending,
    t,
  ]);

  const pendingN = pending?.pending ?? 0;
  // Disabled while the kick-off POST is in flight OR the background
  // batch is grinding (the server would 409 a second run anyway).
  const isRunning = redistillMut.isPending || progress.isRunning;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-medium">{t("settings.redistill.title")}</p>
          <p className="text-xs text-muted-foreground">
            {t("settings.redistill.description", { count: pendingN })}
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="gap-1.5"
          onClick={() => redistillMut.mutate()}
          disabled={isRunning || pendingN === 0}
          data-testid="redistill-pending"
        >
          {isRunning ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          {isRunning ? t("settings.redistill.running") : t("settings.redistill.button")}
        </Button>
      </div>

      {feedback?.kind === "keyInvalid" && (
        <div
          className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
          role="alert"
          data-testid="redistill-key-invalid"
        >
          <p className="font-medium">{t("settings.redistill.keyInvalidTitle")}</p>
          <p className="mt-1 text-muted-foreground">{t("settings.redistill.keyInvalidHint")}</p>
          {feedback.text && (
            <span className="mt-1 block font-mono text-[10px] opacity-80">{feedback.text}</span>
          )}
        </div>
      )}

      {feedback && feedback.kind !== "keyInvalid" && (
        <div
          className={cn(
            "inline-flex items-start gap-1.5 rounded-md border px-2 py-1 text-xs",
            feedback.kind === "success"
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300"
              : "border-destructive/40 bg-destructive/10 text-destructive",
          )}
          role="status"
          aria-live="polite"
          data-testid="redistill-feedback"
        >
          {feedback.kind === "success" ? (
            <Check className="mt-0.5 h-3 w-3 shrink-0" />
          ) : (
            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          )}
          <span>{feedback.text}</span>
        </div>
      )}
    </div>
  );
}
