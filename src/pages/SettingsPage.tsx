import { useEffect, useState } from "react";
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
  RefreshCw,
  ChevronDown,
  Loader2,
  Check,
  AlertCircle,
} from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import type { Theme } from "@/lib/theme";
import { useLanguage } from "@/hooks/useLanguage";
import { LANGUAGE_LABELS, SUPPORTED_LANGUAGES, type Language } from "@/lib/language";
import { cn } from "@/lib/utils";
import type { LlmConfigUpdate, ProviderId, ProviderSchema } from "@/types";

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

  const { data: llmConfig } = useQuery({
    queryKey: ["llmConfig"],
    queryFn: () => api.getLlmConfig(),
  });
  const { data: schemas } = useQuery({
    queryKey: ["providerSchemas"],
    queryFn: () => api.listProviders(),
    staleTime: 5 * 60_000,
  });

  // Active form state. `null` until the config query resolves; the render
  // path falls back to a default provider in that window so the UI never
  // shows a blank dropdown.
  const [selectedProvider, setSelectedProvider] = useState<ProviderId | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  // "Clear key" is a destructive action — track it as a flag so we only
  // emit apiKey="" on the next save, not on every render.
  const [clearKey, setClearKey] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  // Hydrate the form when the loaded config first arrives. After that the
  // form is owned by local state so the user can edit freely.
  useEffect(() => {
    if (selectedProvider === null && llmConfig) {
      setSelectedProvider(llmConfig.provider);
      setModel(llmConfig.model ?? "");
      setBaseUrl(llmConfig.baseUrl ?? "");
    }
  }, [llmConfig, selectedProvider]);

  // Switching providers resets the form fields. Anything the user typed
  // but didn't save is discarded — clearer than carrying stale state into
  // a different provider's settings.
  const onProviderChange = (next: ProviderId) => {
    setSelectedProvider(next);
    setApiKey("");
    setClearKey(false);
    if (llmConfig && llmConfig.provider === next) {
      setModel(llmConfig.model ?? "");
      setBaseUrl(llmConfig.baseUrl ?? "");
    } else {
      const schema = schemas?.find((s) => s.id === next);
      setModel(schema?.defaultModel ?? "");
      setBaseUrl("");
    }
  };

  // Effective provider for rendering. Until the config loads we show
  // deepseek (the v0.2a default) so the form has stable, sensible
  // defaults.
  const activeProvider: ProviderId = selectedProvider ?? "deepseek";
  const activeSchema: ProviderSchema | undefined = schemas?.find(
    (s) => s.id === activeProvider,
  );
  const requiresKey = activeSchema?.requiresKey ?? true;

  // Field visibility per provider. Mirrors docs/v0.2a/providers-design.md.
  const showApiKey = requiresKey;
  const showOllamaHost = activeProvider === "ollama";
  const showBaseUrl = activeProvider === "custom";
  // For the 3 key providers the model lives behind an "Advanced" disclosure;
  // ollama and custom show the model directly because the user is more likely
  // to want to change it (custom's model is required, ollama's is the whole
  // point of the setup).
  const showAdvancedModel = activeProvider !== "ollama" && activeProvider !== "custom";
  const showInlineModel = activeProvider === "ollama" || activeProvider === "custom";

  const apiKeyPlaceholderKey = (() => {
    switch (activeProvider) {
      case "deepseek":
        return "settings.provider.apiKeyPlaceholderDeepseek";
      case "openai":
        return "settings.provider.apiKeyPlaceholderOpenai";
      case "anthropic":
        return "settings.provider.apiKeyPlaceholderAnthropic";
      case "custom":
        return "settings.provider.apiKeyPlaceholderCustom";
      default:
        return "settings.provider.apiKeyPlaceholderDeepseek";
    }
  })();

  const saveMut = useMutation({
    mutationFn: () => {
      if (!selectedProvider) throw new Error("No provider selected");
      const update: LlmConfigUpdate = { provider: selectedProvider };

      if (showApiKey) {
        if (clearKey) {
          // Explicit clear — empty string signals "wipe this slot" to
          // both the Tauri command and the HTTP endpoint.
          update.apiKey = "";
        } else if (apiKey.trim()) {
          update.apiKey = apiKey.trim();
        }
      }
      if (showOllamaHost && baseUrl.trim()) {
        update.baseUrl = baseUrl.trim();
      }
      if (showBaseUrl && baseUrl.trim()) {
        update.baseUrl = baseUrl.trim();
      }
      if (model.trim()) {
        update.model = model.trim();
      }
      return api.setLlmConfig(update);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["llmConfig"] });
      setFeedback({ kind: "success", text: t("settings.provider.saveSuccess") });
      setApiKey("");
      setClearKey(false);
      window.setTimeout(() => setFeedback(null), 3_000);
    },
    onError: (e) => {
      console.error("[prism] setLlmConfig failed:", e);
      setFeedback({ kind: "error", text: t("settings.provider.saveError") });
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

  const configured = llmConfig?.configured ?? false;
  const currentLabel = activeSchema?.label ?? activeProvider;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" />
            <CardTitle className="text-base">{t("settings.aiSection")}</CardTitle>
          </div>
          <Badge
            variant={configured ? "default" : "secondary"}
            data-testid="llm-config-status"
            data-configured={configured}
          >
            {configured
              ? `✅ ${t("settings.apiKeyStatus.configured")}`
              : `❌ ${t("settings.apiKeyStatus.notConfigured")}`}
          </Badge>
        </div>
        <CardDescription>
          {t("settings.provider.current", { label: currentLabel })}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Provider dropdown */}
        <div className="space-y-1.5">
          <label
            htmlFor="provider-select"
            className="text-sm font-medium"
          >
            {t("settings.provider.label")}
          </label>
          <select
            id="provider-select"
            data-testid="provider-select"
            value={activeProvider}
            onChange={(e) => onProviderChange(e.target.value as ProviderId)}
            disabled={saveMut.isPending}
            className={cn(
              "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {(schemas ?? []).map((schema) => (
              <option key={schema.id} value={schema.id}>
                {schema.label}
              </option>
            ))}
          </select>
          {activeSchema && (
            <p
              className="text-xs text-muted-foreground"
              data-testid="provider-hint"
              data-provider={activeProvider}
            >
              {t(`settings.provider.hints.${activeProvider}` as const)}
            </p>
          )}
        </div>

        {/* API key (for the 3 key providers + custom) */}
        {showApiKey && (
          <div className="space-y-1.5">
            <label
              htmlFor="provider-api-key"
              className="text-sm font-medium"
            >
              {t("settings.provider.apiKey")}
            </label>
            <div className="flex items-center gap-2">
              <Input
                id="provider-api-key"
                data-testid="provider-api-key"
                type="password"
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  if (clearKey) setClearKey(false);
                }}
                placeholder={t(apiKeyPlaceholderKey as any)}
                disabled={saveMut.isPending}
                autoComplete="off"
                spellCheck={false}
                className="flex-1"
              />
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  setApiKey("");
                  setClearKey(true);
                }}
                disabled={saveMut.isPending}
                data-testid="provider-clear-key"
              >
                {t("settings.provider.clearKey")}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {t("settings.apiKeyDescription")}
            </p>
          </div>
        )}

        {/* No key needed hint for Ollama */}
        {!showApiKey && (
          <p className="text-xs text-muted-foreground" data-testid="provider-no-key-hint">
            {t("settings.provider.noKeyNeeded")}
          </p>
        )}

        {/* Ollama host — only when the user picked ollama */}
        {showOllamaHost && (
          <div className="space-y-1.5">
            <label
              htmlFor="provider-ollama-host"
              className="text-sm font-medium"
            >
              {t("settings.provider.ollamaHost")}
            </label>
            <Input
              id="provider-ollama-host"
              data-testid="provider-ollama-host"
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://127.0.0.1:11434"
              disabled={saveMut.isPending}
              autoComplete="off"
              spellCheck={false}
            />
            <p className="text-xs text-muted-foreground">
              {t("settings.provider.ollamaHostHint")}
            </p>
          </div>
        )}

        {/* Base URL — only when the user picked custom */}
        {showBaseUrl && (
          <div className="space-y-1.5">
            <label
              htmlFor="provider-base-url"
              className="text-sm font-medium"
            >
              {t("settings.provider.baseUrl")}
            </label>
            <Input
              id="provider-base-url"
              data-testid="provider-base-url"
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={t("settings.provider.baseUrlPlaceholder")}
              disabled={saveMut.isPending}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
        )}

        {/* Model — inline for ollama and custom, advanced disclosure otherwise */}
        {showInlineModel && (
          <div className="space-y-1.5">
            <label htmlFor="provider-model" className="text-sm font-medium">
              {t("settings.provider.model")}
            </label>
            <Input
              id="provider-model"
              data-testid="provider-model"
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={activeSchema?.defaultModel ?? ""}
              disabled={saveMut.isPending}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
        )}

        {showAdvancedModel && (
          <details
            className="rounded-md border border-input bg-background/50 p-3"
            data-testid="provider-advanced"
          >
            <summary className="flex cursor-pointer items-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground">
              <ChevronDown className="h-3.5 w-3.5" />
              {t("settings.provider.advanced")}
            </summary>
            <div className="mt-3 space-y-1.5">
              <label htmlFor="provider-model" className="text-sm font-medium">
                {t("settings.provider.model")}
              </label>
              <Input
                id="provider-model"
                data-testid="provider-model"
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={activeSchema?.defaultModel ?? ""}
                disabled={saveMut.isPending}
                autoComplete="off"
                spellCheck={false}
              />
            </div>
          </details>
        )}

        {/* Save button */}
        <div className="flex items-center gap-2">
          <Button
            type="button"
            onClick={() => saveMut.mutate()}
            disabled={saveMut.isPending}
            data-testid="provider-save"
          >
            {saveMut.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {saveMut.isPending
              ? t("settings.provider.saving")
              : t("settings.provider.save")}
          </Button>
        </div>

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

        <Separator />

        {/* Manual sync — kept from v0.2a for users who don't want to wait
            for the scheduler. Lives in the AI section because in practice
            "re-run the pipeline" is what you do after fixing the provider. */}
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
      </CardContent>
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
