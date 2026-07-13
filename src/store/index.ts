import { create } from "zustand";
import {
  getStoredNotificationsEnabled,
  setStoredNotificationsEnabled,
} from "@/lib/notifications";

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
  // v0.5: filter the inbox to items carrying this user tag. null = no tag
  // filter. Independent of the source filter (both narrow the same list).
  tagFilter: string | null;
  // v0.5: "keyword" = FTS5 substring search; "semantic" = sqlite-vec KNN
  // over MiniMax embeddings. Only affects the inbox when a query is typed.
  searchMode: "keyword" | "semantic";

  // Command palette (⌘K) — a global overlay, so its open state lives here
  // rather than in one page: the shortcut listener, the TopBar affordance,
  // and the palette itself all read/write the same flag.
  commandPaletteOpen: boolean;

  // v0.5 notifications. `notificationsEnabled` is the persisted opt-in.
  // `lastManualJobId` is the job the user kicked off via "Sync now"; the
  // notification hook skips it so a manual sync (already toasted in-app)
  // doesn't also fire an OS notification.
  notificationsEnabled: boolean;
  lastManualJobId: string | null;

  // Actions
  setSelectedSource: (id: string | null) => void;
  setSelectedItem: (id: string | null) => void;
  setSearchQuery: (q: string) => void;
  setStatusFilter: (s: PrismState["statusFilter"]) => void;
  setTagFilter: (tag: string | null) => void;
  setSearchMode: (m: PrismState["searchMode"]) => void;
  setCommandPaletteOpen: (open: boolean) => void;
  setNotificationsEnabled: (on: boolean) => void;
  setLastManualJobId: (id: string | null) => void;
}

export const usePrismStore = create<PrismState>((set) => ({
  selectedSourceId: null,
  selectedItemId: null,
  searchQuery: "",
  statusFilter: "all",
  tagFilter: null,
  searchMode: "keyword",
  commandPaletteOpen: false,
  notificationsEnabled: getStoredNotificationsEnabled(),
  lastManualJobId: null,

  setSelectedSource: (id) => set({ selectedSourceId: id, selectedItemId: null }),
  setSelectedItem: (id) => set({ selectedItemId: id }),
  setSearchQuery: (q) => set({ searchQuery: q }),
  setStatusFilter: (s) => set({ statusFilter: s }),
  setTagFilter: (tag) => set({ tagFilter: tag }),
  setSearchMode: (m) => set({ searchMode: m }),
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
  setNotificationsEnabled: (on) => {
    setStoredNotificationsEnabled(on);
    set({ notificationsEnabled: on });
  },
  setLastManualJobId: (id) => set({ lastManualJobId: id }),
}));
