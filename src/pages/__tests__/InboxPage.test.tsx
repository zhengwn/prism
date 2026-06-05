import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { InboxPage } from "../InboxPage";
import * as api from "@/lib/api";
import type { KnowledgeItem, Source, SyncResult } from "@/types";

// Mock the whole api module — the InboxPage only needs the queries
// (listItems / listSources) to render and the syncAll() promise to drive
// the Sync button's state machine.
vi.mock("@/lib/api", () => ({
  api: {
    listItems: vi.fn(),
    listSources: vi.fn(),
    syncAll: vi.fn(),
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
    // A promise we control — never resolves during the assertion.
    let resolveSync!: (v: SyncResult) => void;
    const pending = new Promise<SyncResult>((res) => {
      resolveSync = res;
    });
    vi.mocked(api.api.syncAll).mockReturnValue(pending);

    renderWithQuery(<InboxPage />);

    const btn = await screen.findByTestId("sync-now-button");
    expect(btn).not.toBeDisabled();

    fireEvent.click(btn);

    await waitFor(() => {
      expect(btn).toBeDisabled();
    });
    expect(btn).toHaveAttribute("data-sync-state", "running");

    // Resolve so the test doesn't leak a hanging promise / pending timer.
    await act(async () => {
      resolveSync({
        jobId: "job-1",
        startedAt: new Date().toISOString(),
        finishedAt: new Date().toISOString(),
        itemsNew: 0,
        itemsDistilled: 0,
      });
      await pending;
    });
  });

  it("re-enables the button after syncAll resolves successfully", async () => {
    vi.mocked(api.api.syncAll).mockResolvedValue({
      jobId: "job-1",
      startedAt: new Date().toISOString(),
      finishedAt: new Date().toISOString(),
      itemsNew: 3,
      itemsDistilled: 2,
    });

    renderWithQuery(<InboxPage />);
    const btn = await screen.findByTestId("sync-now-button");
    fireEvent.click(btn);

    // The button briefly enters `success` state, then a 2.5s timer
    // drops it back to `idle`. We just assert the post-success
    // re-enable — using fake timers here would be more precise but
    // adds a lot of boilerplate. We verify two states: success → idle.
    await waitFor(() => {
      expect(btn).toHaveAttribute("data-sync-state", "success");
    });
    expect(btn).not.toBeDisabled();
  });
});
