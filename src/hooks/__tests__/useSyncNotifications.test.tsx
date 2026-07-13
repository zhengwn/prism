import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useSyncNotifications } from "../useSyncNotifications";
import { usePrismStore } from "@/store";
import * as api from "@/lib/api";
import * as notifications from "@/lib/notifications";
import type { SyncResult } from "@/types";

vi.mock("@/lib/api", () => ({
  api: { getSyncJobs: vi.fn() },
  isTauri: () => false,
  SIDECAR_BASE: "http://127.0.0.1:8765",
}));

// Keep the real getStored/setStored (the store imports them); stub notify.
vi.mock("@/lib/notifications", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/notifications")>();
  return { ...actual, notify: vi.fn() };
});

function job(jobId: string, itemsNew: number, status: SyncResult["status"] = "done"): SyncResult {
  return {
    jobId, startedAt: new Date().toISOString(), finishedAt: new Date().toISOString(),
    status, sourcesTotal: 1, sourcesDone: 1, itemsNew, itemsDistilled: itemsNew,
  };
}

function Harness() {
  useSyncNotifications();
  return null;
}

let qc: QueryClient;
function renderHarness() {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Harness />
    </QueryClientProvider>,
  );
}

async function refetch() {
  await act(async () => {
    await qc.invalidateQueries({ queryKey: ["sync-jobs-notify"] });
  });
}

describe("useSyncNotifications", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    usePrismStore.setState({ notificationsEnabled: true, lastManualJobId: null });
  });

  it("seeds on first data without notifying", async () => {
    const j1 = job("j1", 5);
    vi.mocked(api.api.getSyncJobs).mockResolvedValue([j1]);
    renderHarness();
    await waitFor(() => expect(qc.getQueryData(["sync-jobs-notify"])).toEqual([j1]));
    expect(notifications.notify).not.toHaveBeenCalled();
  });

  it("notifies on a newer finished job with new items", async () => {
    const j1 = job("j1", 0);
    let current = [j1];
    vi.mocked(api.api.getSyncJobs).mockImplementation(async () => current);
    renderHarness();
    // Wait until the first data has seeded before switching to a new job.
    await waitFor(() => expect(qc.getQueryData(["sync-jobs-notify"])).toEqual([j1]));

    current = [job("j2", 5)];
    await refetch();

    await waitFor(() => expect(notifications.notify).toHaveBeenCalledTimes(1));
  });

  it("does not notify for the user's manual sync job", async () => {
    const j1 = job("j1", 0);
    let current = [j1];
    vi.mocked(api.api.getSyncJobs).mockImplementation(async () => current);
    renderHarness();
    await waitFor(() => expect(qc.getQueryData(["sync-jobs-notify"])).toEqual([j1]));

    act(() => usePrismStore.setState({ lastManualJobId: "j2" }));
    current = [job("j2", 5)];
    await refetch();
    await waitFor(() => expect(api.api.getSyncJobs).toHaveBeenCalledTimes(2));
    expect(notifications.notify).not.toHaveBeenCalled();
  });

  it("does not notify when the newest job brought no new items", async () => {
    const j1 = job("j1", 0);
    let current = [j1];
    vi.mocked(api.api.getSyncJobs).mockImplementation(async () => current);
    renderHarness();
    await waitFor(() => expect(qc.getQueryData(["sync-jobs-notify"])).toEqual([j1]));

    current = [job("j2", 0)];
    await refetch();
    await waitFor(() => expect(api.api.getSyncJobs).toHaveBeenCalledTimes(2));
    expect(notifications.notify).not.toHaveBeenCalled();
  });

  it("does nothing while notifications are disabled", async () => {
    usePrismStore.setState({ notificationsEnabled: false });
    vi.mocked(api.api.getSyncJobs).mockResolvedValue([job("j1", 5)]);
    renderHarness();
    // The query is disabled, so getSyncJobs is never called.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(api.api.getSyncJobs).not.toHaveBeenCalled();
    expect(notifications.notify).not.toHaveBeenCalled();
  });
});
