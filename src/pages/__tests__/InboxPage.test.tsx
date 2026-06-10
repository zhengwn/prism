import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { InboxPage } from "../InboxPage";
import * as api from "@/lib/api";
import type { KnowledgeItem, Source, SyncResult } from "@/types";

// Mock the whole api module — the InboxPage only needs the queries
// (listItems / listSources) to render and the syncAll() promise to drive
// the Sync button's state machine. The distill-progress hook is also
// consumed now (for the live progress strip), so we mock the new
// endpoints with no-op defaults that resolve to an "idle" snapshot.
vi.mock("@/lib/api", () => ({
  api: {
    listItems: vi.fn(),
    listSources: vi.fn(),
    syncAll: vi.fn(),
    // v0.2b: the page polls getSyncStatus to know when a fire-and-
    // forget sync finishes. Default mock: returns a job that's
    // already done, so the post-click poll loop exits after one tick.
    getSyncStatus: vi.fn(),
    cancelSync: vi.fn(),
    getDistillStatus: vi.fn(() =>
      Promise.resolve({
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
      }),
    ),
    subscribeDistillProgress: vi.fn(() => () => {}),
  },
  SIDECAR_BASE: "http://127.0.0.1:8765",
  PrismAPIError: class PrismAPIError extends Error {
    constructor(public status: number, message: string) {
      super(message);
      this.name = "PrismAPIError";
    }
  },
}));

const FAKE_SOURCES: Source[] = [
  { id: "src-1", name: "Hacker News", kind: "x", url: "https://hn.example", enabled: true, itemCount: 3 },
  { id: "src-2", name: "Simon Willison", kind: "rss", url: "https://simon.example", enabled: true, itemCount: 2 },
];

const FAKE_ITEMS: KnowledgeItem[] = [
  {
    id: "it-1",
    sourceId: "src-1",
    sourceName: "Hacker News",
    url: "https://hn.example/1",
    titleEn: "Hello",
    titleZh: "你好",
    title: "你好",
    summaryEn: "summary",
    summaryZh: "摘要",
    summary: "摘要",
    keyPointsZh: ["要点1"],
    keyPoints: ["要点1"],
    tagsZh: ["标签1"],
    tags: ["标签1"],
    publishedAt: new Date().toISOString(),
    fetchedAt: new Date().toISOString(),
    status: "unread",
    contentType: "post",
  },
];

function renderWithQuery(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("InboxPage — Sync now button", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.api.listItems).mockResolvedValue(FAKE_ITEMS);
    vi.mocked(api.api.listSources).mockResolvedValue(FAKE_SOURCES);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("disables the button while syncAll() is in flight", async () => {
    // v0.2b: /api/sync returns immediately with status=running;
    // the page polls getSyncStatus to know when the job settles.
    // We mock the job as "still running" so the button stays in
    // its running state for the duration of the test.
    const runningJob: SyncResult = {
      jobId: "job-1",
      startedAt: new Date().toISOString(),
      finishedAt: null,
      status: "running",
      sourcesTotal: 0,
      sourcesDone: 0,
      itemsNew: 0,
      itemsDistilled: 0,
    };
    vi.mocked(api.api.syncAll).mockResolvedValue(runningJob);
    vi.mocked(api.api.getSyncStatus).mockResolvedValue(runningJob);

    renderWithQuery(<InboxPage />);

    const btn = await screen.findByTestId("sync-now-button");
    expect(btn).not.toBeDisabled();

    fireEvent.click(btn);

    await waitFor(() => {
      expect(btn).toHaveAttribute("data-sync-state", "running");
    });
  });

  it("re-enables the button after syncAll resolves successfully", async () => {
    const runningJob: SyncResult = {
      jobId: "job-1",
      startedAt: new Date().toISOString(),
      finishedAt: null,
      status: "running",
      sourcesTotal: 0,
      sourcesDone: 0,
      itemsNew: 0,
      itemsDistilled: 0,
    };
    vi.mocked(api.api.syncAll).mockResolvedValue(runningJob);
    // First poll returns "running" (so we exercise the loop);
    // every subsequent poll returns "done" with the final stats.
    let pollCount = 0;
    vi.mocked(api.api.getSyncStatus).mockImplementation(async () => {
      pollCount += 1;
      if (pollCount < 2) return runningJob;
      return {
        jobId: "job-1",
        startedAt: new Date().toISOString(),
        finishedAt: new Date().toISOString(),
        status: "done",
        sourcesTotal: 1,
        sourcesDone: 1,
        itemsNew: 3,
        itemsDistilled: 2,
      };
    });

    renderWithQuery(<InboxPage />);
    const btn = await screen.findByTestId("sync-now-button");
    fireEvent.click(btn);

    // After the poll resolves, the button briefly enters `success`
    // state, then a 2.5s timer drops it back to `idle`. We just
    // assert the post-success re-enable.
    await waitFor(() => {
      expect(btn).toHaveAttribute("data-sync-state", "success");
    });
    expect(btn).not.toBeDisabled();
  });
});
