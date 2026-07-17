import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Plug } from "lucide-react";
import { useLanguage } from "@/hooks/useLanguage";

export function IntegrationsCard() {
  const { t } = useLanguage();

  return (
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
