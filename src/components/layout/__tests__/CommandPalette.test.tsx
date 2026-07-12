import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { CommandPalette } from "../CommandPalette";
import { usePrismStore } from "@/store";
import * as api from "@/lib/api";
import type { KnowledgeItem, Source } from "@/types";

// Mock the api module — the palette only reads listSources (for the source
// jump entries) and listItems (FTS search for item entries).
vi.mock("@/lib/api", () => ({
  api: {
    listSources: vi.fn(),
    listItems: vi.fn(),
  },
  SIDECAR_BASE: "http://127.0.0.1:8765",
}));

const FAKE_SOURCES: Source[] = [
  { id: "src-1", name: "Hacker News", kind: "rss", url: "https://hn.example", enabled: true, itemCount: 3 },
  { id: "src-2", name: "Simon Willison", kind: "rss", url: "https://simon.example", enabled: true, itemCount: 2 },
];

const FAKE_ITEMS: KnowledgeItem[] = [
  {
    id: "it-42",
    sourceId: "src-1",
    sourceName: "Hacker News",
    url: "https://hn.example/42",
    titleEn: "A distilled headline",
    title: "A distilled headline",
    publishedAt: new Date().toISOString(),
    fetchedAt: new Date().toISOString(),
    status: "unread",
    contentType: "post",
  },
];

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname}</div>;
}

function renderPalette({ open = false }: { open?: boolean } = {}) {
  // Set the open flag BEFORE mounting so the overlay renders open with no
  // post-mount state change — that keeps the interaction tests free of act()
  // warnings (the ⌘K path is exercised separately, wrapped in act()).
  if (open) usePrismStore.setState({ commandPaletteOpen: true });
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <MemoryRouter initialEntries={["/inbox"]}>
      <QueryClientProvider client={qc}>
        <CommandPalette />
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("CommandPalette", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.api.listSources).mockResolvedValue(FAKE_SOURCES);
    vi.mocked(api.api.listItems).mockResolvedValue(FAKE_ITEMS);
    // Reset the shared store between tests (Zustand is a singleton).
    usePrismStore.setState({
      commandPaletteOpen: false,
      selectedSourceId: null,
      selectedItemId: null,
    });
    // Reset global theme side-effects (the palette applies theme to <html> +
    // localStorage directly).
    document.documentElement.classList.remove("dark");
    localStorage.clear();
  });

  it("is closed by default and opens on ⌘K", async () => {
    renderPalette();
    expect(screen.queryByTestId("command-palette")).toBeNull();

    // The ⌘K listener is a native window listener, so wrap the resulting
    // store update in act() to keep React happy.
    act(() => {
      fireEvent.keyDown(document.body, { key: "k", metaKey: true });
    });

    expect(await screen.findByTestId("command-palette")).toBeInTheDocument();
    // Empty query shows the static command sections.
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
    expect(document.querySelector('[data-command-item="nav-inbox"]')).not.toBeNull();
  });

  it("closes on Escape", async () => {
    renderPalette();
    act(() => {
      fireEvent.keyDown(document.body, { key: "k", ctrlKey: true });
    });
    const input = await screen.findByTestId("command-palette-input");

    fireEvent.keyDown(input, { key: "Escape" });

    await waitFor(() => expect(screen.queryByTestId("command-palette")).toBeNull());
  });

  it("filters commands by the typed query", async () => {
    renderPalette({ open: true });
    const input = await screen.findByTestId("command-palette-input");

    fireEvent.change(input, { target: { value: "inbox" } });

    // nav-inbox matches; nav-knowledge does not.
    expect(document.querySelector('[data-command-item="nav-inbox"]')).not.toBeNull();
    expect(document.querySelector('[data-command-item="nav-knowledge"]')).toBeNull();
    // Let the debounced item query settle inside act() so its trailing
    // setState doesn't leak past the test.
    await waitFor(() => expect(api.api.listItems).toHaveBeenCalledWith({ q: "inbox", limit: 8 }));
  });

  it("runs a navigation entry on click and closes", async () => {
    renderPalette({ open: true });
    await screen.findByTestId("command-palette");

    const goSources = document.querySelector<HTMLButtonElement>('[data-command-item="nav-sources"]')!;
    fireEvent.click(goSources);

    expect(screen.getByTestId("loc").textContent).toBe("/sources");
    await waitFor(() => expect(screen.queryByTestId("command-palette")).toBeNull());
  });

  it("navigates with arrow keys + Enter", async () => {
    renderPalette({ open: true });
    const input = await screen.findByTestId("command-palette-input");

    // Flat order starts at nav-inbox (0); two downs → nav-sources (2).
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(screen.getByTestId("loc").textContent).toBe("/sources");
  });

  it("surfaces FTS item results and jumps to the item", async () => {
    renderPalette({ open: true });
    const input = await screen.findByTestId("command-palette-input");

    fireEvent.change(input, { target: { value: "distilled" } });

    // Item search is debounced (180ms) then resolves from the mock.
    const itemEntry = await waitFor(() => {
      const el = document.querySelector<HTMLButtonElement>('[data-command-item="item-it-42"]');
      expect(el).not.toBeNull();
      return el!;
    });

    fireEvent.click(itemEntry);
    expect(usePrismStore.getState().selectedItemId).toBe("it-42");
    expect(screen.getByTestId("loc").textContent).toBe("/inbox");
  });

  it("applies a theme change that survives the palette closing", async () => {
    // Regression: the palette runs an entry then closes on the same tick, so
    // it unmounts immediately. Theme must apply via the synchronous global
    // helpers (DOM class + storage), not a component-scoped useEffect that
    // the unmount would discard.
    renderPalette({ open: true });
    await screen.findByTestId("command-palette");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    const darkEntry = document.querySelector<HTMLButtonElement>('[data-command-item="act-theme-dark"]')!;
    fireEvent.click(darkEntry);

    // Applied globally and persisted, even though the overlay just unmounted.
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("prism-theme")).toBe("dark");
    await waitFor(() => expect(screen.queryByTestId("command-palette")).toBeNull());
  });

  it("jumps to a source filter when a source entry is chosen", async () => {
    renderPalette({ open: true });
    const input = await screen.findByTestId("command-palette-input");

    fireEvent.change(input, { target: { value: "simon" } });

    const srcEntry = await waitFor(() => {
      const el = document.querySelector<HTMLButtonElement>('[data-command-item="src-src-2"]');
      expect(el).not.toBeNull();
      return el!;
    });
    fireEvent.click(srcEntry);

    expect(usePrismStore.getState().selectedSourceId).toBe("src-2");
    expect(screen.getByTestId("loc").textContent).toBe("/inbox");
  });
});
