import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, SIDECAR_BASE } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Cpu, RefreshCw, Loader2 } from "lucide-react";
import { useLanguage } from "@/hooks/useLanguage";
import { Row } from "./Row";

export function SidecarStatusCard() {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    refetchInterval: 10_000,
  });

  // Manual sidecar restart (Settings → Sidecar card). Picks up a
  // newly-saved key / hand-edited keystore without relaunching the app.
  // The Tauri command returns immediately (background respawn), so after
  // it resolves we wait a beat for the new process to bind, then refetch
  // health so the version/uptime row reflects the fresh process.
  const restartMut = useMutation({
    mutationFn: () => api.restartSidecar(),
    onSuccess: async () => {
      await new Promise((r) => setTimeout(r, 1500));
      await queryClient.invalidateQueries({ queryKey: ["health"] });
    },
  });

  return (
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
        <Separator className="my-1" />
        <div className="flex items-center justify-between gap-3 pt-1">
          <p className="text-xs text-muted-foreground">
            {t("settings.restartSidecarHint")}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => restartMut.mutate()}
            disabled={restartMut.isPending}
            className="shrink-0"
          >
            {restartMut.isPending ? (
              <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-3.5 w-3.5" />
            )}
            {restartMut.isPending
              ? t("settings.restartSidecarPending")
              : t("settings.restartSidecar")}
          </Button>
        </div>
        {restartMut.isError && (
          <p className="text-xs text-destructive">
            {t("settings.restartSidecarError")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
