import { create } from "zustand";
import type { KnowledgeItem, Source } from "@/types";

interface PrismState {
  // Selection
  selectedSourceId: string | null;
  selectedItemId: string | null;

  // Filters
  searchQuery: string;
  statusFilter: "all" | "unread" | "starred" | "archived";

  // Cached data (populated by TanStack Query; mirrored here for cross-component access)
  sources: Source[];
  items: KnowledgeItem[];

  // Actions
  setSelectedSource: (id: string | null) => void;
  setSelectedItem: (id: string | null) => void;
  setSearchQuery: (q: string) => void;
  setStatusFilter: (s: PrismState["statusFilter"]) => void;
  setSources: (sources: Source[]) => void;
  setItems: (items: KnowledgeItem[]) => void;
}

export const usePrismStore = create<PrismState>((set) => ({
  selectedSourceId: null,
  selectedItemId: null,
  searchQuery: "",
  statusFilter: "all",
  sources: [],
  items: [],

  setSelectedSource: (id) => set({ selectedSourceId: id, selectedItemId: null }),
  setSelectedItem: (id) => set({ selectedItemId: id }),
  setSearchQuery: (q) => set({ searchQuery: q }),
  setStatusFilter: (s) => set({ statusFilter: s }),
  setSources: (sources) => set({ sources }),
  setItems: (items) => set({ items }),
}));
