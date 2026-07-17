import { Trans } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Palette, Sun, Moon, Monitor } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import type { Theme } from "@/lib/theme";
import { useLanguage } from "@/hooks/useLanguage";
import { cn } from "@/lib/utils";

export function AppearanceCard() {
  const { t } = useLanguage();
  const { theme, setTheme, resolvedTheme } = useTheme();

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Palette className="h-4 w-4" />
          <CardTitle className="text-base">{t("theme.cardTitle")}</CardTitle>
        </div>
        <CardDescription>
          {/* <em>System</em> needs inline emphasis, so we use Trans for the
              embedded markup rather than splitting into two strings. */}
          <Trans i18nKey="theme.cardDescription" components={{ 1: <em /> }} />
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
