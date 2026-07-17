import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { invoke } from "@tauri-apps/api/core";
import { api, isTauri } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  Sparkles,
  RefreshCw,
  Loader2,
  Check,
  AlertCircle,
  Eye,
  EyeOff,
} from "lucide-react";
import { useLanguage } from "@/hooks/useLanguage";
import { cn } from "@/lib/utils";
import type { LlmConfigUpdate, ProviderId, ProviderSchema } from "@/types";
import { RedistillBlock } from "./RedistillBlock";

export function AiSection() {
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
  // API key input has three visual modes:
  //   "hidden"   — a key is on disk but the eye is closed. The field
  //                shows a length-matched dot mask (one `•` per char
  //                of the real key, derived from `llmConfig.keyLength`)
  //                so the input width tracks the actual secret instead
  //                of a fixed 8-dot placeholder. Plaintext is NOT in
  //                renderer state — the only way to get the real value
  //                is the eye toggle, which calls `reveal_llm_key` on
  //                demand.
  //   "revealed" — the user explicitly opened the eye; the field shows
  //                the full key (type=text). Read-only so the user
  //                can't accidentally edit it. Closing the eye clears
  //                the value from React state immediately.
  //   "editing"  — the user is typing a new key (or there is no key on
  //                disk). Empty field, password-masked, normal edit.
  // We default to "editing" so the field has predictable behaviour
  // before `llmConfig` resolves; the first render after hydration may
  // flip us into "hidden" if the active provider already has a key.
  const [apiKeyMode, setApiKeyMode] = useState<"hidden" | "revealed" | "editing">("editing");
  // The plaintext key, only populated while the eye is open or the
  // user is mid-typing. Never derived from `llmConfig` (that never
  // carries the key — only `configured` and `keyLast4`).
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  // "Clear key" is a destructive action — track it as a flag so we only
  // emit apiKey="" on the next save, not on every render.
  const [clearKey, setClearKey] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  // Unmount guard for the manual-sync poll loop below: the job keeps
  // running server-side, we just stop hammering the API for a UI that
  // is no longer on screen.
  const aliveRef = useRef(true);
  useEffect(() => {
    return () => {
      aliveRef.current = false;
    };
  }, []);

  // Hydrate the form when the loaded config first arrives. After that the
  // form is owned by local state so the user can edit freely.
  useEffect(() => {
    if (selectedProvider === null && llmConfig) {
      setSelectedProvider(llmConfig.provider);
      setModel(llmConfig.model ?? "");
      setBaseUrl(llmConfig.baseUrl ?? "");
      // If the active provider already has a key on disk, land in
      // "hidden" so the placeholder dots appear — the user knows a key
      // is there but the plaintext isn't sitting in React state until
      // they click the eye.
      if (llmConfig.configured) {
        setApiKeyMode("hidden");
        setApiKey("");
      } else {
        setApiKeyMode("editing");
      }
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
      setApiKeyMode(llmConfig.configured ? "hidden" : "editing");
    } else {
      const schema = schemas?.find((s) => s.id === next);
      setModel(schema?.defaultModel ?? "");
      setBaseUrl("");
      setApiKeyMode("editing");
    }
  };

  // Eye toggle: pulls the key from the Tauri command only when the
  // user explicitly opens the eye. Closing the eye wipes the value
  // from renderer state so the secret stops living in the React tree.
  const [revealPending, setRevealPending] = useState(false);
  const onToggleReveal = async () => {
    if (apiKeyMode === "revealed") {
      // Was visible — collapse back to hidden and drop the plaintext.
      setApiKey("");
      setApiKeyMode("hidden");
      return;
    }
    // Was hidden or editing. Need a key on disk to reveal — if there
    // isn't one, flip into editing instead so the user can type a
    // brand-new one without confusion.
    if (!llmConfig?.configured || !selectedProvider) {
      setApiKeyMode("editing");
      setApiKey("");
      return;
    }
    // Pure-Vite dev: no Tauri shell → no keystore to reveal from.
    // Bail into editing instead of letting `invoke` throw
    // "__TAURI_INTERNALS__ is undefined" into the console.
    if (!isTauri()) {
      setApiKeyMode("editing");
      setApiKey("");
      return;
    }
    setRevealPending(true);
    try {
      const full = await invoke<string | null>("reveal_llm_key", {
        provider: selectedProvider,
      });
      if (full != null) {
        setApiKey(full);
        setApiKeyMode("revealed");
      } else {
        // Keystore lost the key between config-fetch and reveal-call
        // (rare — file deleted out from under us). Fall back to
        // editing so the user can re-enter.
        setApiKeyMode("editing");
      }
    } catch (e) {
      console.error("[prism] reveal_llm_key failed:", e);
      setApiKeyMode("editing");
    } finally {
      setRevealPending(false);
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

  // Field visibility per provider. v0.2a+: only 2 providers, both
  // key-required. The model field is inline (no "Advanced" disclosure)
  // because both providers are the same shape — a disclosure adds no
  // value when there's nothing to hide.
  const showApiKeyField = requiresKey;

  // Placeholder is provider-specific ("sk-…" for DeepSeek, "ey…" for
  // MiniMax) when no key is configured, and a friendly "saved" hint
  // once a key is in the keystore so the user knows they don't need to
  // re-type it unless they want to rotate. Resolved lazily via a
  // function (not a const) because the underlying `configured` flag
  // arrives later in the render via `llmConfig`.
  const apiKeyPlaceholder = (isConfigured: boolean) =>
    isConfigured
      ? t("settings.provider.apiKeyPlaceholderSaved")
      : t(
          activeProvider === "minimax"
            ? "settings.provider.apiKeyPlaceholderMinimax"
            : "settings.provider.apiKeyPlaceholderDeepseek",
        );

  // Length-matched password mask. We render `keyLength` bullet chars
  // (one per character of the real key) so the field width tracks the
  // actual secret — short keys don't stretch the input, long keys
  // don't get truncated. Falls back to a short placeholder if the
  // keystore hasn't told us the length yet (race between the first
  // `get_llm_config` reply and the first render).
  const HIDDEN_KEY_MASK_FALLBACK = "••••••••";
  const hiddenKeyMask =
    typeof llmConfig?.keyLength === "number" && llmConfig.keyLength > 0
      ? "•".repeat(llmConfig.keyLength)
      : HIDDEN_KEY_MASK_FALLBACK;

  const saveMut = useMutation({
    mutationFn: () => {
      if (!selectedProvider) throw new Error("No provider selected");
      const update: LlmConfigUpdate = { provider: selectedProvider };

      if (showApiKeyField) {
        if (clearKey) {
          // Explicit clear — empty string signals "wipe this slot" to
          // both the Tauri command and the HTTP endpoint.
          update.apiKey = "";
        } else if (apiKey.trim()) {
          update.apiKey = apiKey.trim();
        }
      }
      if (baseUrl.trim()) {
        // Power-user override — the sidecar schema doesn't surface this
        // field in the UI but the active-provider marker accepts it.
        update.baseUrl = baseUrl.trim();
      }
      if (model.trim()) {
        update.model = model.trim();
      }
      return api.setLlmConfig(update);
    },
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ["llmConfig"] });
      setFeedback({ kind: "success", text: t("settings.provider.saveSuccess") });
      setApiKey("");
      setClearKey(false);
      // After a save, drop back into "hidden" so the freshly written
      // key is masked again. The user can re-reveal explicitly via the
      // eye toggle. Landing in "revealed" would leave the plaintext
      // sitting in renderer state until the user does something.
      setApiKeyMode(resp.configured ? "hidden" : "editing");
      window.setTimeout(() => setFeedback(null), 3_000);
    },
    onError: (e) => {
      console.error("[prism] setLlmConfig failed:", e);
      setFeedback({ kind: "error", text: t("settings.provider.saveError") });
    },
  });

  // v0.2b made /api/sync asynchronous: the POST returns immediately with
  // status="running" and itemsNew=0 while the pipeline runs in the
  // background. Poll the job until it settles (same pattern as
  // InboxPage's handleSync) so the feedback shows the real counters —
  // the pre-fix code toasted the initial response, which always read
  // "0 new / 0 distilled".
  const syncMut = useMutation({
    mutationFn: async () => {
      const initial = await api.syncAll();
      const POLL_MS = 500;
      const deadline = Date.now() + 5 * 60_000;
      let final = initial;
      while (final.status === "running" && Date.now() < deadline && aliveRef.current) {
        await new Promise<void>((r) => window.setTimeout(r, POLL_MS));
        final = await api.getSyncStatus(initial.jobId);
      }
      return final;
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
      if (r.status === "error") {
        setFeedback({ kind: "error", text: t("inbox.syncError") });
        return;
      }
      setFeedback({
        kind: "success",
        text:
          r.status === "running"
            ? // Poll deadline hit — the job is still grinding in the
              // background; don't fake a completed result.
              t("inbox.syncStillRunning")
            : t("inbox.syncResult", { new: r.itemsNew, distilled: r.itemsDistilled }),
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

        {/* API key (both providers are key-required) */}
        {showApiKeyField && (
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
                // Two-state visibility: hidden → type=password + a
                // length-matched dot mask (plaintext is NOT in renderer
                // state); revealed → type=text + real key. Editing is
                // the third mode (password + whatever the user typed).
                type={apiKeyMode === "revealed" ? "text" : "password"}
                value={
                  apiKeyMode === "hidden"
                    ? hiddenKeyMask
                    : apiKey
                }
                readOnly={apiKeyMode === "revealed" || apiKeyMode === "hidden"}
                onFocus={() => {
                  // Focusing into "hidden" while a key is on disk should
                  // land in editing — typing replaces the existing key.
                  // Don't auto-reveal on focus (privacy: shoulder
                  // surfing / focus-stomping).
                  if (apiKeyMode === "hidden") {
                    setApiKeyMode("editing");
                    setApiKey("");
                    setClearKey(false);
                  }
                }}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  if (clearKey) setClearKey(false);
                }}
                placeholder={
                  apiKeyMode === "editing"
                    ? apiKeyPlaceholder(llmConfig?.configured ?? false)
                    : ""
                }
                disabled={saveMut.isPending}
                autoComplete="off"
                spellCheck={false}
                className={cn(
                  "flex-1",
                  apiKeyMode === "hidden" && "font-mono tracking-widest",
                  apiKeyMode === "revealed" && "font-mono",
                )}
              />
              <Button
                type="button"
                size="icon"
                variant="ghost"
                onClick={onToggleReveal}
                disabled={saveMut.isPending || revealPending}
                aria-label={t(apiKeyMode === "revealed" ? "settings.provider.hideApiKey" : "settings.provider.showApiKey")}
                title={t(apiKeyMode === "revealed" ? "settings.provider.hideApiKey" : "settings.provider.showApiKey")}
                data-testid="provider-toggle-key"
              >
                {revealPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : apiKeyMode === "revealed" ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  setApiKey("");
                  setClearKey(true);
                  // Wipe is a destructive break-glass — drop out of
                  // hidden/revealed mode so the empty value renders
                  // unambiguously as "empty", not as the mask or the
                  // exposed plaintext.
                  setApiKeyMode("editing");
                }}
                disabled={saveMut.isPending}
                data-testid="provider-clear-key"
              >
                {t("settings.provider.clearKey")}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {(llmConfig?.configured ?? false)
                ? t("settings.apiKeyDescriptionConfigured")
                : t("settings.apiKeyDescription")}
            </p>
          </div>
        )}

        {/* Model — inline for both providers (v0.2a+ has no "advanced"
            disclosure; both providers are the same shape). The
            `defaultModel` from the schema is the user-facing id
            (e.g. "M3" or "deepseek-v4-pro") — the distiller prepends
            the litellm routing prefix internally. */}
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
