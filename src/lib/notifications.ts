/**
 * OS notifications (v0.5). Two backends behind one API:
 *   - Tauri shell: `@tauri-apps/plugin-notification` (imported lazily so
 *     browser dev never loads it).
 *   - Browser dev: the standard `window.Notification` Web API.
 *
 * The user opts in from Settings; we only fire when the sync-notification
 * hook detects a background sync that brought in new items.
 */

import { isTauri } from "@/lib/api";

export const NOTIFICATIONS_KEY = "prism-notifications";

export function getStoredNotificationsEnabled(): boolean {
  try {
    return localStorage.getItem(NOTIFICATIONS_KEY) === "1";
  } catch {
    return false;
  }
}

export function setStoredNotificationsEnabled(on: boolean): void {
  try {
    localStorage.setItem(NOTIFICATIONS_KEY, on ? "1" : "0");
  } catch {
    // Storage disabled (private mode / quota) — the toggle still works for
    // this session, it just won't persist.
  }
}

/** Request permission if needed. Returns whether notifications are allowed. */
export async function ensureNotificationPermission(): Promise<boolean> {
  if (isTauri()) {
    const { isPermissionGranted, requestPermission } = await import(
      "@tauri-apps/plugin-notification"
    );
    if (await isPermissionGranted()) return true;
    return (await requestPermission()) === "granted";
  }
  if (typeof Notification === "undefined") return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  return (await Notification.requestPermission()) === "granted";
}

/** Fire a notification if permission is granted (no-op otherwise). */
export async function notify(title: string, body: string): Promise<void> {
  if (isTauri()) {
    const { isPermissionGranted, sendNotification } = await import(
      "@tauri-apps/plugin-notification"
    );
    if (await isPermissionGranted()) sendNotification({ title, body });
    return;
  }
  if (typeof Notification !== "undefined" && Notification.permission === "granted") {
    new Notification(title, { body });
  }
}
