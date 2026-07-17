import { useLanguage } from "@/hooks/useLanguage";
import { type Language } from "@/lib/language";
import { AppearanceCard } from "./settings/AppearanceCard";
import { LanguageCard } from "./settings/LanguageCard";
import { NotificationsCard } from "./settings/NotificationsCard";
import { AiSection } from "./settings/AiSection";
import { SidecarStatusCard } from "./settings/SidecarStatusCard";
import { IntegrationsCard } from "./settings/IntegrationsCard";
import { DevToolsCard } from "./settings/DevToolsCard";

/**
 * Settings — composition shell. Each card owns its own data fetching and
 * local state; this file is only page chrome + ordering. The cards live
 * in ./settings/ (split out of what used to be a single 1100-line file):
 *
 *   AppearanceCard    — theme radio group (light / dark / system)
 *   LanguageCard      — UI language radio group
 *   NotificationsCard — OS-notification opt-in toggle (v0.5)
 *   AiSection         — LLM provider config + manual sync + redistill
 *   SidecarStatusCard — health readout + Apply & Restart Sidecar
 *   IntegrationsCard  — MCP / Skill / LLM integration status rows
 *   DevToolsCard      — log / reset / docs pointers
 */
export function SettingsPage() {
  const { t } = useLanguage();

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-3xl space-y-6">
        <div>
          <h2 className="text-lg font-semibold">{t("settings.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("settings.description")}</p>
        </div>

        <AppearanceCard />
        <LanguageCard />
        <NotificationsCard />
        <AiSection />
        <SidecarStatusCard />
        <IntegrationsCard />
        <DevToolsCard />
      </div>
    </div>
  );
}

// `Language` type is re-exported for any sibling file that wants to constrain
// the value, e.g. an MCP tool that accepts a language code.
export type { Language };
