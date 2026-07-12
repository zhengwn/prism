import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DetailPanel } from "../DetailPanel";
import { usePrismStore } from "@/store";
import * as api from "@/lib/api";
import type { KnowledgeItem } from "@/types";

vi.mock("@/lib/api", () => ({
  api: {
    getItem: vi.fn(),
    updateItemStatus: vi.fn(),
    addItemTag: vi.fn(),
    removeItemTag: vi.fn(),
  },
  SIDECAR_BASE: "http://127.0.0.1:8765",
  PrismAPIError: class PrismAPIError extends Error {
    constructor(public status: number, message: string) {
      super(message);
      this.name = "PrismAPIError";
    }
  },
}));

const FAKE_BILI_ITEM: KnowledgeItem = {
  id: "it-bili",
  sourceId: "src-bili",
  sourceName: "智东西",
  url: "https://www.bilibili.com/video/BV1xx411c7mD",
  titleEn: "AI video",
  titleZh: "AI 视频",
  title: "AI 视频",
  summaryEn: "summary",
  summaryZh: "摘要",
  summary: "摘要",
  keyPointsZh: ["要点1"],
  keyPoints: ["要点1"],
  tagsZh: ["AI"],
  tags: ["AI"],
  publishedAt: new Date().toISOString(),
  fetchedAt: new Date().toISOString(),
  status: "unread",
  contentType: "video",
  metadataJson: { bvid: "BV1xx411c7mD" },
};

function renderWithQuery(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("DetailPanel — Bilibili item", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // The panel auto-marks unread items as read on open; give the
    // status mutation a resolvable default so that background call
    // never rejects in tests that don't care about it.
    vi.mocked(api.api.updateItemStatus).mockImplementation(
      async (_id: string, status) => ({ ...FAKE_BILI_ITEM, status }),
    );
    usePrismStore.setState({ selectedItemId: null });
  });

  it("renders an iframe pointing at the official Bilibili player when bvid is present", async () => {
    vi.mocked(api.api.getItem).mockResolvedValue(FAKE_BILI_ITEM);
    usePrismStore.setState({ selectedItemId: FAKE_BILI_ITEM.id });

    renderWithQuery(<DetailPanel />);

    const player = await screen.findByTestId("detail-bilibili-player");
    expect(player).toBeInTheDocument();
    const iframe = player.querySelector("iframe");
    expect(iframe).not.toBeNull();
    expect(iframe?.getAttribute("src")).toBe(
      "https://player.bilibili.com/player.html?bvid=BV1xx411c7mD&autoplay=0",
    );
  });

  it("renders an 'Open on Bilibili' link in the footer for B station items", async () => {
    vi.mocked(api.api.getItem).mockResolvedValue(FAKE_BILI_ITEM);
    usePrismStore.setState({ selectedItemId: FAKE_BILI_ITEM.id });

    renderWithQuery(<DetailPanel />);

    const openLink = await screen.findByTestId("detail-open-bilibili");
    expect(openLink).toBeInTheDocument();
    expect(openLink.getAttribute("href")).toBe(FAKE_BILI_ITEM.url);
  });

  it("falls back to a 'missing BV' hint + link when the B station URL has no bvid", async () => {
    // Same kind, but no top-level bvid AND a mid-page URL — the
    // player can't render so the panel degrades to the missing
    // hint while the footer still exposes the "Open on Bilibili"
    // link.
    const midOnlyItem: KnowledgeItem = {
      ...FAKE_BILI_ITEM,
      url: "https://space.bilibili.com/339137722",
      metadataJson: {},
    };
    vi.mocked(api.api.getItem).mockResolvedValue(midOnlyItem);
    usePrismStore.setState({ selectedItemId: midOnlyItem.id });

    renderWithQuery(<DetailPanel />);

    const missing = await screen.findByTestId("detail-bilibili-missing");
    expect(missing).toBeInTheDocument();
    const openLink = screen.getByTestId("detail-open-bilibili");
    expect(openLink.getAttribute("href")).toBe(midOnlyItem.url);
  });

  it("does not render the Bilibili player for non-B station items", async () => {
    const rssItem: KnowledgeItem = {
      ...FAKE_BILI_ITEM,
      id: "it-rss",
      url: "https://example.com/post",
      metadataJson: {},
    };
    vi.mocked(api.api.getItem).mockResolvedValue(rssItem);
    usePrismStore.setState({ selectedItemId: rssItem.id });

    renderWithQuery(<DetailPanel />);

    // Wait for the open-original footer link to appear (signals
    // the item finished loading), then assert the Bilibili-
    // specific bits are absent.
    await waitFor(() => {
      expect(screen.getByTestId("detail-open-original")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("detail-bilibili-player")).toBeNull();
    expect(screen.queryByTestId("detail-open-bilibili")).toBeNull();
  });
});

describe("DetailPanel — user tags", () => {
  const TAGGED: KnowledgeItem = {
    ...FAKE_BILI_ITEM,
    id: "it-tagged",
    url: "https://example.com/post",
    metadataJson: {},
    userTags: ["读过"],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.api.updateItemStatus).mockImplementation(
      async (_id: string, status) => ({ ...TAGGED, status }),
    );
    vi.mocked(api.api.getItem).mockResolvedValue(TAGGED);
    vi.mocked(api.api.addItemTag).mockResolvedValue({ ...TAGGED, userTags: ["读过", "新"] });
    vi.mocked(api.api.removeItemTag).mockResolvedValue({ ...TAGGED, userTags: [] });
    usePrismStore.setState({ selectedItemId: TAGGED.id });
  });

  it("renders existing user tags as chips", async () => {
    renderWithQuery(<DetailPanel />);
    expect(await screen.findByTestId("user-tag-读过")).toBeInTheDocument();
  });

  it("adds a tag on Enter and clears the input", async () => {
    renderWithQuery(<DetailPanel />);
    const input = (await screen.findByTestId("add-tag-input")) as HTMLInputElement;

    fireEvent.change(input, { target: { value: "  新  " } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      // trimmed before sending
      expect(api.api.addItemTag).toHaveBeenCalledWith(TAGGED.id, "新"),
    );
    expect(input.value).toBe("");
  });

  it("does not submit an empty/whitespace tag", async () => {
    renderWithQuery(<DetailPanel />);
    const input = await screen.findByTestId("add-tag-input");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(api.api.addItemTag).not.toHaveBeenCalled();
  });

  it("removes a tag when its × is clicked", async () => {
    renderWithQuery(<DetailPanel />);
    const chip = await screen.findByTestId("user-tag-读过");
    fireEvent.click(chip.querySelector("button")!);
    await waitFor(() =>
      expect(api.api.removeItemTag).toHaveBeenCalledWith(TAGGED.id, "读过"),
    );
  });
});