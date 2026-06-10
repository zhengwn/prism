// Live distill progress hook — wraps the sidecar's
// `GET /api/distill/status/stream` Server-Sent Events endpoint and
// exposes the latest snapshot to React components.
//
// Design
// ------
// We seed the hook with a one-shot `GET /api/distill/status` so a
// late-mounting component (e.g. user opens the inbox mid-run) sees
// the current state immediately, with no flash of "idle". Then we
// open the SSE stream for live updates.
//
// The hook is intentionally **read-only**: it surfaces whatever
// the sidecar publishes and does no polling, no exponential
// backoff, no manual reconnect. The browser's `EventSource`
// already does all of that — and the sidecar closes the stream
// cleanly when a run ends (the `isRunning=false` event is the
// last one), so we don't even need to detect "the run is done" by
// timing: the stream simply stops emitting.
//
// Cleanup
// -------
// The hook's `useEffect` returns the `EventSource.close()` handle
// so unmounting the consuming component tears down the connection
// — important for performance when many components subscribe in
// turn (e.g. test harnesses).
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DistillProgress } from "@/types";

const IDLE_SNAPSHOT: DistillProgress = {
  isRunning: false,
  pending: 0,
  distilled: 0,
  failed: 0,
  currentTitle: null,
  currentSource: null,
  startedAt: null,
  finishedAt: null,
  lastEventAt: 0,
  lastError: null,
};

/**
 * Subscribe to the sidecar's live distill progress.
 *
 * Returns the latest snapshot (re-rendering on every event). When
 * the sidecar isn't up or no run is in flight, returns an idle
 * snapshot — the consuming component decides how to render it
 * (typically: hide the progress bar).
 */
export function useDistillProgress(): DistillProgress {
  const [snap, setSnap] = useState<DistillProgress>(IDLE_SNAPSHOT);

  useEffect(() => {
    let alive = true;
    let unsubscribe: (() => void) | null = null;

    // Best-effort seed: if the sidecar is up, grab the current
    // snapshot synchronously so the user doesn't see a flash of
    // idle. If it's down (e.g. cold start), swallow the error —
    // the SSE stream will reconnect once the sidecar comes back.
    api
      .getDistillStatus()
      .then((initial) => {
        if (alive) setSnap(initial);
      })
      .catch(() => {
        /* sidecar not ready yet — SSE will catch up */
      });

    unsubscribe = api.subscribeDistillProgress((next) => {
      if (alive) setSnap(next);
    });

    return () => {
      alive = false;
      unsubscribe?.();
    };
  }, []);

  return snap;
}
