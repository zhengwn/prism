import { Search, RefreshCw, Command, Sun, Moon, Monitor } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { usePrismStore } from "@/store";
import { useTheme } from "@/hooks/useTheme";
import { useLanguage } from "@/hooks/useLanguage";
import { api } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import type { Theme } from "@/lib/theme";

export function TopBar() {
  const searchQuery = usePrismStore((s) => s.searchQuery);
  const setSearchQuery = usePrismStore((s) => s.setSearchQuery);
  const { theme, setTheme } = useTheme();
  const qc = useQueryClient();
  const { t } = useLanguage();

  const handleSync = async () => {
    try {
      await api.syncAll();
      // Refetch items after sync
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
    } catch (e) {
      console.error("Sync failed:", e);
    }
  };

  const cycleTheme = () => {
    // light -> dark -> system -> light
    const next: Theme = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
    setTheme(next);
  };

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-card/30 px-4">
      {/* Search */}
      <div className="relative flex-1 max-w-xl">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder={t("search.placeholder")}
          className="h-8 pl-8 pr-16 text-sm"
        />
        <kbd className="pointer-events-none absolute right-2 top-1/2 hidden h-5 -translate-y-1/2 select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] font-medium opacity-100 sm:flex">
          <Command className="h-2.5 w-2.5" />K
        </kbd>
      </div>

      <div className="flex-1" />

      {/* Actions */}
      <Button variant="ghost" size="sm" onClick={handleSync} className="gap-1.5">
        <RefreshCw className="h-3.5 w-3.5" />
        {t("actions.sync")}
      </Button>
      <ThemeToggle theme={theme} onCycle={cycleTheme} />
    </header>
  );
}

function ThemeToggle({
  theme,
  onCycle,
}: {
  theme: Theme;
  onCycle: () => void;
}) {
  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  const label =
    theme === "light"
      ? "Light — click for dark"
      : theme === "dark"
      ? "Dark — click for system"
      : "System — click for light";
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={onCycle}
      title={label}
      aria-label={label}
      className="h-8 w-8"
    >
      <Icon className="h-3.5 w-3.5" />
    </Button>
  );
}
