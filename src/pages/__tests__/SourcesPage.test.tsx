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
});
