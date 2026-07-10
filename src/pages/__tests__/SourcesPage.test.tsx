import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SourcesPage } from "../SourcesPage";
import * as api from "@/lib/api";
import type { Source } from "@/types";

vi.mock("@/lib/api", () => ({
  api: {
    listSources: vi.fn(),
    listItems: vi.fn(),
    createSource: vi.fn(),
    deleteSource: vi.fn(),
    patchSource: vi.fn(),
  },
  SIDECAR_BASE: "http://127.0.0.1:8765",
  PrismAPIError: class PrismAPIError extends Error {
    constructor(public status: number, message: string) {
      super(message);
      this.name = "PrismAPIError";
    }
  },
}));

const FAKE_SOURCES: Source[] = [];

function renderWithQuery(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("SourcesPage — Add Source dialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.api.listSources).mockResolvedValue(FAKE_SOURCES);
    vi.mocked(api.api.listItems).mockResolvedValue([]);
  });

  it("opens the dialog when the + button is clicked", async () => {
    renderWithQuery(<SourcesPage />);
    const addBtn = await screen.findByTestId("add-source-button");
    fireEvent.click(addBtn);
    expect(await screen.findByTestId("add-source-dialog")).toBeInTheDocument();
  });

  it("calls api.createSource with the form values on submit", async () => {
    const created: Source = {
      id: "src-new",
      name: "My Feed",
      kind: "rss",
      url: "https://example.com/feed.xml",
      enabled: true,
      itemCount: 0,
    };
    vi.mocked(api.api.createSource).mockResolvedValue(created);

    renderWithQuery(<SourcesPage />);
    fireEvent.click(await screen.findByTestId("add-source-button"));

    const nameInput = await screen.findByTestId("add-source-name");
    const urlInput = screen.getByTestId("add-source-url");
    const submit = await screen.findByTestId("add-source-submit");

    fireEvent.change(nameInput, { target: { value: "My Feed" } });
    fireEvent.change(urlInput, { target: { value: "https://example.com/feed.xml" } });
    fireEvent.click(submit);

    await waitFor(() => {
      expect(api.api.createSource).toHaveBeenCalledWith({
        name: "My Feed",
        kind: "rss",
        url: "https://example.com/feed.xml",
        enabled: true,
      });
    });
  });

  it("lists Bilibili as a kind option in the Add Source dialog", async () => {
    renderWithQuery(<SourcesPage />);
    fireEvent.click(await screen.findByTestId("add-source-button"));
    const select = await screen.findByTestId("add-source-kind") as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toContain("bilibili");
  });

  it("submits a Bilibili source with the bvid inside configJson when kind=Bilibili", async () => {
    const created: Source = {
      id: "src-bili",
      name: "智东西",
      kind: "bilibili",
      url: "https://www.bilibili.com/video/BV1xx411c7mD",
      enabled: true,
      itemCount: 0,
      configJson: { bvid: "BV1xx411c7mD" },
    };
    vi.mocked(api.api.createSource).mockResolvedValue(created);

    renderWithQuery(<SourcesPage />);
    fireEvent.click(await screen.findByTestId("add-source-button"));

    const select = await screen.findByTestId("add-source-kind") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "bilibili" } });

    const nameInput = screen.getByTestId("add-source-name");
    const urlInput = screen.getByTestId("add-source-url");
    const submit = screen.getByTestId("add-source-submit");

    fireEvent.change(nameInput, { target: { value: "智东西" } });
    fireEvent.change(urlInput, {
      target: { value: "https://www.bilibili.com/video/BV1xx411c7mD" },
    });
    fireEvent.click(submit);

    await waitFor(() => {
      // The bvid MUST travel inside configJson — the sidecar's
      // SourceCreate model ignores unknown top-level keys, and the
      // BilibiliFetcher reads only config_json.
      expect(api.api.createSource).toHaveBeenCalledWith({
        name: "智东西",
        kind: "bilibili",
        url: "https://www.bilibili.com/video/BV1xx411c7mD",
        enabled: true,
        configJson: { bvid: "BV1xx411c7mD" },
      });
    });
  });

  it("renders a Bilibili type badge for an existing B station source", async () => {
    const bili: Source = {
      id: "src-bili",
      name: "机器之心",
      kind: "bilibili",
      url: "https://www.bilibili.com/video/BV1abc2345de",
      enabled: true,
      itemCount: 12,
      configJson: { bvid: "BV1abc2345de" },
    };
    vi.mocked(api.api.listSources).mockResolvedValue([bili]);

    renderWithQuery(<SourcesPage />);
    const badge = await screen.findByTestId("source-bilibili-badge");
    expect(badge).toBeInTheDocument();
    expect(badge.textContent).toMatch(/Bilibili|B站/);
  });
});
