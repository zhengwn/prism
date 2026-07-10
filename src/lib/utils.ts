import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Minimal shape of react-i18next's `t()` — avoids pulling an i18next
 * dependency into this module just for a type. */
type Translate = (key: string, opts?: Record<string, unknown>) => string;

/**
 * Format a date as a human-readable relative time string.
 * Defensive against undefined / null / invalid dates — returns "" instead
 * of crashing. The webview's dark body background makes a render crash look
 * like a black screen, so any unhandled throw here is a UX disaster.
 *
 * Takes `t` from `useLanguage()` so the output is localized — this used to
 * hard-code English ("3h ago" etc.) regardless of the active UI language,
 * which violates the project's own i18n invariant (AGENTS.md: "every
 * user-visible string ... must go through t()"). Callers: InboxPage's
 * ItemRow, SourcesPage's SourceCard, DetailPanel's header.
 */
export function formatRelativeTime(
  date: string | Date | null | undefined,
  t: Translate,
): string {
  if (!date) return "";
  const d = typeof date === "string" ? new Date(date) : date;
  if (isNaN(d.getTime())) return "";
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);

  if (diffMin < 1) return t("time.justNow");
  if (diffMin < 60) return t("time.minutesAgo", { count: diffMin });
  if (diffHr < 24) return t("time.hoursAgo", { count: diffHr });
  if (diffDay < 7) return t("time.daysAgo", { count: diffDay });
  return d.toLocaleDateString();
}
