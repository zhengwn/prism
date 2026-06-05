import { useQuery } from "@tanstack/react-query";
import { api, SIDECAR_BASE } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Cpu, Plug, Wrench, Sun, Moon, Monitor, Palette } from "lucide-react";
import { useThemeStore } from "@/store/theme";
import type { Theme } from "@/lib/theme-runtime";
import { cn } from "@/lib/utils";

export function SettingsPage() {
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    refetchInterval: 10_000,
  });

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-3xl space-y-6">
        <div>
          <h2 className="text-lg font-semibold">Settings</h2>
          <p className="text-sm text-muted-foreground">
            Prism status, integration points, and developer tools.
          </p>
        </div>

        {/* Appearance */}
        <AppearanceCard />

        {/* Status */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Cpu className="h-4 w-4" />
                <CardTitle className="text-base">Sidecar Status</CardTitle>
              </div>
              <Badge variant={health?.ok ? "default" : "destructive"}>
                {health?.ok ? "Healthy" : "Unreachable"}
              </Badge>
            </div>
            <CardDescription>
              The Python sidecar handles content fetching, LLM distillation, and MCP.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Row label="Endpoint" value={SIDECAR_BASE} mono />
            <Row label="Version" value={health?.version ?? "—"} mono />
            <Row label="Sources" value={String(health?.sourcesCount ?? 0)} />
            <Row label="Items" value={String(health?.itemsCount ?? 0)} />
            <Row
              label="Uptime"
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
              <CardTitle className="text-base">Integrations</CardTitle>
            </div>
            <CardDescription>
              Connect Prism to your AI agents.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <IntegrationRow
              title="MCP Server"
              status="coming-soon"
              description="Expose Prism's knowledge as MCP tools (read/search/subscribe)."
            />
            <Separator />
            <IntegrationRow
              title="Agent Skill"
              status="coming-soon"
              description="A reusable skill bundle (Mavis / OpenCode / Claude Code / etc.) that wraps Prism's API."
            />
            <Separator />
            <IntegrationRow
              title="LLM Provider"
              status="coming-soon"
              description="OpenAI / Anthropic / Gemini / local Ollama — pick what distills your feeds."
            />
          </CardContent>
        </Card>

        {/* Dev tools */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Wrench className="h-4 w-4" />
              <CardTitle className="text-base">Developer</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Row label="Sidecar log" value="python/prism_sidecar/ — see logs/server.log" />
            <Row label="Reset data" value="rm ~/.prism/prism.db && restart" mono />
            <Row label="Docs" value="docs/ARCHITECTURE.md" mono />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function AppearanceCard() {
  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Palette className="h-4 w-4" />
          <CardTitle className="text-base">Appearance</CardTitle>
        </div>
        <CardDescription>
          Choose how Prism looks. "System" follows your OS appearance.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div
          role="radiogroup"
          aria-label="Color theme"
          className="grid grid-cols-1 gap-2 sm:grid-cols-3"
        >
          <ThemeOption
            value="light"
            current={theme}
            onSelect={setTheme}
            icon={Sun}
            label="Light"
            description="Always light"
          />
          <ThemeOption
            value="dark"
            current={theme}
            onSelect={setTheme}
            icon={Moon}
            label="Dark"
            description="Always dark"
          />
          <ThemeOption
            value="system"
            current={theme}
            onSelect={setTheme}
            icon={Monitor}
            label="System"
            description="Follow OS"
          />
        </div>
      </CardContent>
    </Card>
  );
}

function ThemeOption({
  value,
  current,
  onSelect,
  icon: Icon,
  label,
  description,
}: {
  value: Theme;
  current: Theme;
  onSelect: (t: Theme) => void;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  description: string;
}) {
  const selected = current === value;
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={() => onSelect(value)}
      className={cn(
        "flex items-center gap-3 rounded-md border px-3 py-2.5 text-left transition-colors",
        "hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected
          ? "border-primary bg-accent text-accent-foreground ring-1 ring-primary/40"
          : "border-border bg-card text-card-foreground",
      )}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium leading-tight">{label}</div>
        <div className="text-xs text-muted-foreground">{description}</div>
      </div>
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
}: {
  title: string;
  status: "active" | "coming-soon";
  description: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="space-y-0.5">
        <p className="font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <Badge variant={status === "active" ? "default" : "secondary"}>
        {status === "active" ? "Active" : "Coming soon"}
      </Badge>
    </div>
  );
}
