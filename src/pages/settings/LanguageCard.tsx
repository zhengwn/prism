import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Languages } from "lucide-react";
import { useLanguage } from "@/hooks/useLanguage";
import { LANGUAGE_LABELS, SUPPORTED_LANGUAGES } from "@/lib/language";
import { cn } from "@/lib/utils";

export function LanguageCard() {
  const { t, language, setLanguage } = useLanguage();

  return (
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
  );
}
