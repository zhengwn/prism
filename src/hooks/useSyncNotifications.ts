import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { usePrismStore } from "@/store";
import { useLanguage } from "@/hooks/useLanguage";
import { notify } from "@/lib/notifications";

/**
 * useSyncNotifications — fire an OS notification when a background sync
 * brings in new items.
 *
 * Mounted once (in AppLayout). While notifications are enabled it polls the
 * aggregated sync-jobs list. The newest job id seen at mount is treated as
 * "already known" so we don't notify for runs that predate opening the app;
 * after that, a newer finished job with new items fires one notification —
 * except the job the user kicked off via "Sync now" (that path already shows
 * an in-app toast, so an OS notification would be redundant).
 */
export function useSyncNotifications() {
  const enabled = usePrismStore((s) => s.notificationsEnabled);
  const lastManualJobId = usePrismStore((s) => s.lastManualJobId);
  const { t } = useLanguage();

  const lastSeenRef = useRef<string | null>(null);
  const seededRef = useRef(false);

  const { data: jobs } = useQuery({
    queryKey: ["sync-jobs-notify"],
    queryFn: () => api.getSyncJobs(1),
    enabled,
    refetchInterval: enabled ? 45_000 : false,
  });

  useEffect(() => {
    if (!enabled || !jobs || jobs.length === 0) return;
    const latest = jobs[0];

    // Seed on the first successful fetch — establish "what's already there"
    // without notifying.
    if (!seededRef.current) {
      seededRef.current = true;
      lastSeenRef.current = latest.jobId;
      return;
    }
    if (latest.jobId === lastSeenRef.current) return;
    lastSeenRef.current = latest.jobId;

    if (latest.status === "done" && latest.itemsNew > 0 && latest.jobId !== lastManualJobId) {
      void notify(
        t("notifications.newItemsTitle"),
        t("notifications.newItemsBody", { n: latest.itemsNew }),
      );
    }
  }, [jobs, enabled, lastManualJobId, t]);

  // When the user turns notifications off, forget the seed so re-enabling
  // starts fresh (no backlog notification for what arrived while off).
  useEffect(() => {
    if (!enabled) {
      seededRef.current = false;
      lastSeenRef.current = null;
    }
  }, [enabled]);
}
