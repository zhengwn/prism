import { create } from "zustand";

// UI-only state: selection + filters. Server data (sources / items) is
// owned by TanStack Query's cache (see `useQuery` calls in the pages) —
// this store used to also mirror `sources` / `items` here "for
// cross-component access", but nothing ever wrote to that mirror, so it
// always read back empty. Two sources of truth for the same server data
// invites exactly that kind of silent drift; components that need
// sources/items should read them via `useQuery` (same cache key = same
// data, no extra plumbing).
interface PrismState {
  // Selection
  selectedSourceId: string | null;
  selectedItemId: string | null;

  // Filters
  searchQuery: string;
  statusFilter: "all" | "unread" | "starred" | "archived";

  // Command palette (⌘K) — a global overlay, so its open state lives here
  // rather than in one page: the shortcut listener, the TopBar affordance,
  // and the palette itself all read/write the same flag.
  commandPaletteOpen: boolean;

  // Actions
  setSelectedSource: (id: string | null) => void;
  setSelectedItem: (id: string | null) => void;
  setSearchQuery: (q: string) => void;
  setStatusFilter: (s: PrismState["statusFilter"]) => void;
  setCommandPaletteOpen: (open: boolean) => void;
}

export const usePrismStore = create<PrismState>((set) => ({
  selectedSourceId: null,
  selectedItemId: null,
  searchQuery: "",
  statusFilter: "all",
  commandPaletteOpen: false,

  setSelectedSource: (id) => set({ selectedSourceId: id, selectedItemId: null }),
  setSelectedItem: (id) => set({ selectedItemId: id }),
  setSearchQuery: (q) => set({ searchQuery: q }),
  setStatusFilter: (s) => set({ statusFilter: s }),
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
}));
