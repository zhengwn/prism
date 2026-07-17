import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Bell } from "lucide-react";
import { useLanguage } from "@/hooks/useLanguage";
import { cn } from "@/lib/utils";
import { usePrismStore } from "@/store";
import { ensureNotificationPermission } from "@/lib/notifications";

export function NotificationsCard() {
  const { t } = useLanguage();
  const enabled = usePrismStore((s) => s.notificationsEnabled);
  const setEnabled = usePrismStore((s) => s.setNotificationsEnabled);
  const [denied, setDenied] = useState(false);

  const toggle = async () => {
    if (enabled) {
      setEnabled(false);
      setDenied(false);
      return;
    }
    // Enabling: request OS permission first; only flip on if granted.
    const granted = await ensureNotificationPermission();
    if (granted) {
      setEnabled(true);
      setDenied(false);
    } else {
      setDenied(true);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Bell className="h-4 w-4" />
          <CardTitle className="text-base">{t("notifications.cardTitle")}</CardTitle>
        </div>
        <CardDescription>{t("notifications.cardDescription")}</CardDescription>
      </CardHeader>
      <CardContent>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label={t("notifications.cardTitle")}
          onClick={toggle}
          data-testid="notifications-toggle"
          data-enabled={enabled}
          className={cn(
            "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            enabled ? "bg-primary" : "bg-input",
          )}
        >
          <span
            className={cn(
              "inline-block h-5 w-5 transform rounded-full bg-background shadow transition-transform",
              enabled ? "translate-x-5" : "translate-x-0.5",
            )}
          />
        </button>
        <span className="ml-3 align-middle text-sm text-muted-foreground">
          {enabled ? t("notifications.on") : t("notifications.off")}
        </span>
        {denied && (
          <p className="mt-2 text-xs text-destructive" data-testid="notifications-denied">
            {t("notifications.permissionDenied")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
