import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Wrench } from "lucide-react";
import { useLanguage } from "@/hooks/useLanguage";
import { Row } from "./Row";

export function DevToolsCard() {
  const { t } = useLanguage();

  return (
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
  );
}
