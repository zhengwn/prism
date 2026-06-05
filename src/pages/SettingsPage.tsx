import { useQuery } from "@tanstack/react-query";
import { Trans } from "react-i18next";
import { api, SIDECAR_BASE } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
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
