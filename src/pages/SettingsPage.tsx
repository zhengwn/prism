import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trans } from "react-i18next";
import { api, SIDECAR_BASE } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  Cpu,
  Plug,
  Wrench,
  Palette,
  Sun,
  Moon,
  Monitor,
  Languages,
  Sparkles,
  KeyRound,
  RefreshCw,
  X,
  Loader2,
  Check,
  AlertCircle,
} from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import type { Theme } from "@/lib/theme";
import { useLanguage } from "@/hooks/useLanguage";
import { LANGUAGE_LABELS, SUPPORTED_LANGUAGES, type Language } from "@/lib/language";
import { cn } from "@/lib/utils";

export function SettingsPage() {
  const { t } = useLanguage();
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    refetchInterval: 10_000,
  });
  const { theme, setTheme, resolvedTheme } = useTheme();
  const { language, setLanguage } = useLanguage();

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-3xl space-y-6">
        <div>
          <h2 className="text-lg font-semibold">{t("settings.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("settings.description")}</p>
        </div>

        {/* Appearance */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Palette className="h-4 w-4" />
              <CardTitle className="text-base">{t("theme.cardTitle")}</CardTitle>
            </div>
            <CardDescription>
              {/* <em>System</em> needs inline emphasis, so we use Trans for the
                  embedded markup rather than splitting into two strings. */}
              <Trans
                i18nKey="theme.cardDescription"
                components={{ 1: <em /> }}
              />
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div
              role="radiogroup"
              aria-label={t("theme.cardTitle")}
              className="grid grid-cols-3 gap-2"
              data-testid="theme-picker"
            >
              <ThemeOption
                value="light"
                current={theme}
                resolved={resolvedTheme}
                onSelect={setTheme}
                icon={<Sun className="h-4 w-4" />}
                label={t("theme.optionLight")}
                hint={t("theme.hintLight")}
              />
              <ThemeOption
                value="dark"
                current={theme}
                resolved={resolvedTheme}
                onSelect={setTheme}
                icon={<Moon className="h-4 w-4" />}
                label={t("theme.optionDark")}
                hint={t("theme.hintDark")}
              />
              <ThemeOption
                value="system"
                current={theme}
                resolved={resolvedTheme}
                onSelect={setTheme}
                icon={<Monitor className="h-4 w-4" />}
                label={t("theme.optionSystem")}
                hint={
                  theme === "system"
                    ? t(resolvedTheme === "dark" ? "theme.hintSystemCurrentDark" : "theme.hintSystemCurrentLight")
                    : t("theme.hintSystemAuto")
                }
              />
            </div>
          </CardContent>
        </Card>

        {/* Language */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Languages className="h-4 w-4" />
              <CardTitle className="text-base">{t("language.cardTitle")}</CardTitle>
            </div>
            <CardDescription>{t("language.cardDescription")}</CardDescription>
          </CardHeader>
          <CardContent>
            <div
              role="radiogroup"
              aria-label={t("language.cardTitle")}
              className="grid grid-cols-2 gap-2 sm:max-w-xs"
              data-testid="language-picker"
            >
              {SUPPORTED_LANGUAGES.map((code) => {
                const selected = language === code;
                return (
                  <button
                    key={code}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setLanguage(code)}
                    className={cn(
                      "flex items-center justify-center gap-2 rounded-md border p-3 text-sm font-medium transition-colors",
                      "hover:bg-accent hover:text-accent-foreground",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                      selected
                        ? "border-primary bg-accent text-accent-foreground"
                        : "border-input bg-background",
                    )}
                  >
                    <span>{LANGUAGE_LABELS[code]}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {code}
                    </span>
                  </button>
                );
              })}
            </div>
          </CardContent>
        </Card>

        {/* AI Distillation — v0.2a */}
        <AiSection />

        {/* Status */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Cpu className="h-4 w-4" />
                <CardTitle className="text-base">{t("settings.sidecarTitle")}</CardTitle>
              </div>
              <Badge variant={health?.ok ? "default" : "destructive"}>
                {health?.ok ? t("settings.healthy") : t("settings.unreachable")}
              </Badge>
            </div>
            <CardDescription>{t("settings.sidecarDescription")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Row label={t("settings.endpoint")} value={SIDECAR_BASE} mono />
            <Row label={t("settings.version")} value={health?.version ?? "—"} mono />
            <Row label={t("settings.sourcesCount")} value={String(health?.sourcesCount ?? 0)} />
            <Row label={t("settings.itemsCount")} value={String(health?.itemsCount ?? 0)} />
            <Row
              label={t("settings.uptime")}
              value={
                health
                  ? `${Math.floor(health.uptimeSec / 60)}m ${health.uptimeSec % 60}s`
                  : "—"
              }
            />
          </CardContent>
        </Card>

        {/* Integrations */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Plug className="h-4 w-4" />
              <CardTitle className="text-base">{t("settings.integrationsTitle")}</CardTitle>
            </div>
            <CardDescription>{t("settings.integrationsDescription")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <IntegrationRow
              title={t("settings.integrationMcp")}
              status="coming-soon"
              description={t("settings.integrationMcpDescription")}
              activeLabel={t("settings.active")}
              comingSoonLabel={t("settings.comingSoon")}
            />
            <Separator />
            <IntegrationRow
              title={t("settings.integrationSkill")}
              status="coming-soon"
              description={t("settings.integrationSkillDescription")}
              activeLabel={t("settings.active")}
              comingSoonLabel={t("settings.comingSoon")}
            />
            <Separator />
            <IntegrationRow
              title={t("settings.integrationLlm")}
              status="coming-soon"
              description={t("settings.integrationLlmDescription")}
              activeLabel={t("settings.active")}
              comingSoonLabel={t("settings.comingSoon")}
            />
          </CardContent>
        </Card>

        {/* Dev tools */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Wrench className="h-4 w-4" />
              <CardTitle className="text-base">{t("settings.developerTitle")}</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Row label={t("settings.developerSidecarLog")} value={t("settings.developerSidecarLogValue")} />
            <Row label={t("settings.developerResetData")} value={t("settings.developerResetDataValue")} mono />
            <Row label={t("settings.developerDocs")} value={t("settings.developerDocsValue")} mono />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function AiSection() {
  const { t } = useLanguage();
  const qc = useQueryClient();
  const { data: status } = useQuery({
    queryKey: ["apiKeyStatus"],
    queryFn: () => api.getApiKeyStatus(),
  });
  const [dialogOpen, setDialogOpen] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  const clearMut = useMutation({
    mutationFn: () => api.clearApiKey(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["apiKeyStatus"] });
      setFeedback({ kind: "success", text: t("settings.apiKeyStatus.notConfigured") });
      window.setTimeout(() => setFeedback(null), 2_500);
    },
    onError: (e) => {
      console.error("[prism] clearApiKey failed:", e);
      setFeedback({ kind: "error", text: t("inbox.syncError") });
    },
  });

  const syncMut = useMutation({
    mutationFn: () => api.syncAll(),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
      setFeedback({
        kind: "success",
        text: t("inbox.syncResult", { new: r.itemsNew, distilled: r.itemsDistilled }),
      });
      window.setTimeout(() => setFeedback(null), 3_000);
    },
    onError: (e) => {
      console.error("[prism] syncAll failed:", e);
      setFeedback({ kind: "error", text: t("inbox.syncError") });
    },
  });

  const configured = status?.configured ?? false;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" />
            <CardTitle className="text-base">{t("settings.aiSection")}</CardTitle>
          </div>
          <Badge variant={configured ? "default" : "secondary"} data-testid="api-key-status" data-configured={configured}>
            {configured ? `✅ ${t("settings.apiKeyStatus.configured")}` : `❌ ${t("settings.apiKeyStatus.notConfigured")}`}
          </Badge>
        </div>
        <CardDescription>{t("settings.aiDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={() => setDialogOpen(true)}
            data-testid="set-api-key"
          >
            <KeyRound className="h-3.5 w-3.5" />
            {t("settings.setApiKey")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="gap-1.5 text-muted-foreground"
            onClick={() => clearMut.mutate()}
            disabled={!configured || clearMut.isPending}
          >
            {t("settings.clearApiKey")}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">{t("settings.apiKeyDescription")}</p>

        <Separator />

        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">{t("settings.aiDescription")}</p>
          <Button
            size="sm"
            variant="default"
            className="gap-1.5"
            onClick={() => syncMut.mutate()}
            disabled={syncMut.isPending}
            data-testid="manual-sync"
          >
            {syncMut.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {syncMut.isPending ? t("settings.manualSyncRunning") : t("settings.manualSync")}
          </Button>
        </div>

        <Separator />

        <RedistillBlock />

        {feedback && (
          <div
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs",
              feedback.kind === "success"
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300"
                : "border-destructive/40 bg-destructive/10 text-destructive",
            )}
            role="status"
            aria-live="polite"
            data-testid="ai-feedback"
          >
            {feedback.kind === "success" ? (
              <Check className="h-3 w-3" />
            ) : (
              <AlertCircle className="h-3 w-3" />
            )}
            {feedback.text}
          </div>
        )}
      </CardContent>

      <ApiKeyDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onSaved={() => {
          qc.invalidateQueries({ queryKey: ["apiKeyStatus"] });
          setFeedback({ kind: "success", text: t("settings.apiKeyStatus.configured") });
          window.setTimeout(() => setFeedback(null), 2_500);
        }}
      />
    </Card>
  );
}

function RedistillBlock() {
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

  const redistillMut = useMutation({
    mutationFn: () => api.redistill(),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["distillPending"] });
      void refetchPending();
      if (r.keyInvalid) {
        setFeedback({ kind: "keyInvalid", text: r.error ?? t("settings.redistill.keyInvalidTitle") });
      } else {
        setFeedback({
          kind: r.failed > 0 ? "error" : "success",
          text: t("settings.redistill.result", {
            distilled: r.distilled,
            failed: r.failed,
            started: r.startedPending,
          }),
        });
      }
    },
    onError: (e) => {
      console.error("[prism] redistill failed:", e);
      setFeedback({ kind: "error", text: t("inbox.syncError") });
    },
  });

  const pendingN = pending?.pending ?? 0;
  const isRunning = redistillMut.isPending;

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

function ApiKeyDialog({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const { t } = useLanguage();
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const saveMut = useMutation({
    mutationFn: (k: string) => api.setApiKey(k),
    onSuccess: () => {
      setKey("");
      setError(null);
      onOpenChange(false);
      onSaved();
    },
    onError: (e) => {
      console.error("[prism] setApiKey failed:", e);
      setError(t("inbox.syncError"));
    },
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim()) {
      setError(t("inbox.syncError"));
      return;
    }
    setError(null);
    saveMut.mutate(key.trim());
  };

  const onClose = () => {
    if (saveMut.isPending) return;
    setKey("");
    setError(null);
    onOpenChange(false);
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
      data-testid="api-key-dialog"
    >
      <div
        className="w-full max-w-md rounded-lg border bg-card text-card-foreground shadow-lg"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="api-key-title"
      >
        <form onSubmit={onSubmit}>
          <div className="flex items-center justify-between border-b p-4">
            <h3 id="api-key-title" className="text-sm font-semibold">
              {t("settings.apiKeyDialog.title")}
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
            <p className="text-xs text-muted-foreground">{t("settings.apiKeyDialog.description")}</p>
            <Input
              autoFocus
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={t("settings.apiKeyDialog.placeholder")}
              disabled={saveMut.isPending}
              data-testid="api-key-input"
            />
            {error && (
              <p className="text-xs text-destructive" role="alert">
                {error}
              </p>
            )}
          </div>
          <div className="flex items-center justify-end gap-2 border-t p-4">
            <Button type="button" variant="ghost" onClick={onClose} disabled={saveMut.isPending}>
              {t("settings.apiKeyDialog.cancel")}
            </Button>
            <Button type="submit" disabled={saveMut.isPending} data-testid="api-key-submit">
              {saveMut.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {t("settings.apiKeyDialog.submit")}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ThemeOption({
  value,
  current,
  resolved,
  onSelect,
  icon,
  label,
  hint,
}: {
  value: Theme;
  current: Theme;
  resolved: "light" | "dark";
  onSelect: (t: Theme) => void;
  icon: React.ReactNode;
  label: string;
  hint?: string;
}) {
  const selected = current === value;
  // Pull t() in here too so the sr-only hint can be translated without
  // threading it through props from the parent.
  const { t } = useLanguage();
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={() => onSelect(value)}
      className={cn(
        "flex flex-col items-start gap-1 rounded-md border p-3 text-left transition-colors",
        "hover:bg-accent hover:text-accent-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        selected
          ? "border-primary bg-accent text-accent-foreground"
          : "border-input bg-background",
      )}
    >
      <div className="flex items-center gap-2">
        {icon}
        <span className="text-sm font-medium">{label}</span>
      </div>
      <span className="text-xs text-muted-foreground">{hint}</span>
      {value === "system" && selected && (
        <span className="sr-only">
          {t("theme.hintSystemCurrentLight").startsWith("Currently")
            ? `Currently resolving to ${resolved}.`
            : `当前解析为 ${resolved === "dark" ? "深色" : "浅色"}。`}
        </span>
      )}
    </button>
  );
}

function Row({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className={mono ? "font-mono text-xs" : ""}>{value}</span>
    </div>
  );
}

function IntegrationRow({
  title,
  status,
  description,
  activeLabel,
  comingSoonLabel,
}: {
  title: string;
  status: "active" | "coming-soon";
  description: string;
  activeLabel: string;
  comingSoonLabel: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="space-y-0.5">
        <p className="font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <Badge variant={status === "active" ? "default" : "secondary"}>
        {status === "active" ? activeLabel : comingSoonLabel}
      </Badge>
    </div>
  );
}

// `Language` type is re-exported for any sibling file that wants to constrain
// the value, e.g. an MCP tool that accepts a language code.
export type { Language };
