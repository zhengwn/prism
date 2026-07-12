import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Inbox,
  Library,
  Rss,
  Settings,
  Sun,
  Moon,
  Monitor,
  Languages,
  FileText,
  Search,
} from "lucide-react";
import { api } from "@/lib/api";
import { usePrismStore } from "@/store";
import { useLanguage } from "@/hooks/useLanguage";
import { applyTheme, resolveTheme, setStoredTheme, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import type { KnowledgeItem } from "@/types";

/**
 * ⌘K command palette — a single global overlay that lets the user jump
 * anywhere (pages, sources, individual knowledge items) and run quick
 * actions (theme / language) without reaching for the mouse.
 *
 * Why in-house instead of `cmdk`: the rest of the UI kit is hand-rolled
 * (button / input / scroll-area are all local primitives), and the palette
 * is ~1 screen of straightforward list + keyboard logic. A dependency would
 * be more surface area than the thing it replaces.
 *
 * Open state lives in the Zustand store (`commandPaletteOpen`) because three
 * independent places touch it: the global shortcut listener below, the
 * TopBar affordance, and the overlay's own close paths.
 *
 * This component is mounted once (in AppLayout). It always renders the
 * shortcut listener; the actual overlay is mounted only while open, so the
 * input autofocus + item search run fresh on every open and there is no
 * hidden keyboard trap when closed.
 */
export function CommandPalette() {
  const open = usePrismStore((s) => s.commandPaletteOpen);
  const setOpen = usePrismStore((s) => s.setCommandPaletteOpen);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // ⌘K (mac) / Ctrl+K (win/linux) toggles the palette. preventDefault
      // stops WebKit's built-in behaviour (some builds map ⌘K to the
      // address bar even inside a Tauri webview).
      const isModK = (e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K");
      if (!isModK) return;
      e.preventDefault();
      // Read the flag off the store rather than the closed-over `open` so
      // this listener never goes stale (deps are just the stable setter).
      setOpen(!usePrismStore.getState().commandPaletteOpen);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setOpen]);

  if (!open) return null;
  return <PaletteOverlay onClose={() => setOpen(false)} />;
}

type Section = "navigate" | "actions" | "sources" | "items";

interface Entry {
  id: string;
  section: Section;
  label: string;
  /** Right-aligned secondary text (source name for an item, hint for a source). */
  hint?: string;
  icon: React.ComponentType<{ className?: string }>;
  run: () => void;
}

/** Title to show for an item, following the active UI language. */
function itemTitle(item: KnowledgeItem, preferEn: boolean): string {
  if (preferEn) return item.titleEn || item.title;
  return item.titleZh || item.titleEn || item.title;
}

function PaletteOverlay({ onClose }: { onClose: () => void }) {
  const { t, language, setLanguage } = useLanguage();
  const navigate = useNavigate();
  const preferEn = language === "en";

  // Apply the theme through the global lib/theme helpers rather than the
  // useTheme hook: the hook applies the change inside a useEffect, but this
  // overlay unmounts on the same tick the entry runs (run → onClose), so
  // that effect would never commit and the change would be lost. These
  // helpers touch the DOM class + localStorage synchronously, so the switch
  // sticks regardless of unmount. (setLanguage is already synchronous —
  // i18n.changeLanguage fires immediately, not via an effect.)
  const setTheme = (next: Theme) => {
    setStoredTheme(next);
    applyTheme(resolveTheme(next));
  };

  const setSelectedSource = usePrismStore((s) => s.setSelectedSource);
  const setSelectedItem = usePrismStore((s) => s.setSelectedItem);

  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Focus the input as soon as the overlay mounts.
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Debounce the FTS item search so fast typing doesn't queue a stack of
  // requests that resolve out of order (same reasoning as InboxPage).
  useEffect(() => {
    const h = window.setTimeout(() => setDebounced(query.trim()), 180);
    return () => window.clearTimeout(h);
  }, [query]);

  const { data: sources } = useQuery({
    queryKey: ["sources"],
    queryFn: () => api.listSources(),
  });

  // Item search only fires with a real query, and is capped small — the
  // palette is a jump list, not a browser. Reuses the sidecar FTS5 index.
  const { data: items, isFetching: itemsFetching } = useQuery({
    queryKey: ["palette-items", debounced],
    queryFn: () => api.listItems({ q: debounced, limit: 8 }),
    enabled: debounced.length > 0,
  });

  const q = query.trim().toLowerCase();
  const matches = (label: string) => q === "" || label.toLowerCase().includes(q);

  const navEntries: Entry[] = [
    { id: "nav-inbox", section: "navigate" as const, label: t("commandPalette.goInbox"), icon: Inbox, run: () => navigate("/inbox") },
    { id: "nav-knowledge", section: "navigate" as const, label: t("commandPalette.goKnowledge"), icon: Library, run: () => navigate("/knowledge") },
    { id: "nav-sources", section: "navigate" as const, label: t("commandPalette.goSources"), icon: Rss, run: () => navigate("/sources") },
    { id: "nav-settings", section: "navigate" as const, label: t("commandPalette.goSettings"), icon: Settings, run: () => navigate("/settings") },
  ].filter((e) => matches(e.label));

  const actionEntries: Entry[] = [
    { id: "act-theme-light", section: "actions" as const, label: t("commandPalette.themeLight"), icon: Sun, run: () => setTheme("light") },
    { id: "act-theme-dark", section: "actions" as const, label: t("commandPalette.themeDark"), icon: Moon, run: () => setTheme("dark") },
    { id: "act-theme-system", section: "actions" as const, label: t("commandPalette.themeSystem"), icon: Monitor, run: () => setTheme("system") },
    { id: "act-lang-en", section: "actions" as const, label: t("commandPalette.langEn"), icon: Languages, run: () => setLanguage("en") },
    { id: "act-lang-zh", section: "actions" as const, label: t("commandPalette.langZh"), icon: Languages, run: () => setLanguage("zh") },
  ].filter((e) => matches(e.label));

  // Sources and items only appear once the user has typed something — an
  // empty-query palette is a command menu; a query turns it into search.
  const sourceEntries: Entry[] =
    q === ""
      ? []
      : (sources ?? [])
          .filter((s) => s.name.toLowerCase().includes(q))
          .slice(0, 6)
          .map((s) => ({
            id: `src-${s.id}`,
            section: "sources" as const,
            label: s.name,
            hint: t("commandPalette.filterSource"),
            icon: Rss,
            run: () => {
              setSelectedSource(s.id);
              navigate("/inbox");
            },
          }));

  const itemEntries: Entry[] = (items ?? []).map((it) => ({
    id: `item-${it.id}`,
    section: "items" as const,
    label: itemTitle(it, preferEn),
    hint: it.sourceName,
    icon: FileText,
    run: () => {
      // DetailPanel fetches by id independently of the inbox filter, so the
      // item shows even if the current filter would hide it from the list.
      setSelectedItem(it.id);
      navigate("/inbox");
    },
  }));

  // Flat list, in section order — the index that keyboard nav walks.
  const entries: Entry[] = [...navEntries, ...actionEntries, ...sourceEntries, ...itemEntries];

  // Reset the highlight to the top whenever the query changes so Enter never
  // fires a stale selection from a previous result set.
  useEffect(() => {
    setActive(0);
  }, [query]);

  // Keep the highlighted row in view as the user arrows through.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>('[data-selected="true"]');
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const clampedActive = Math.min(active, Math.max(0, entries.length - 1));

  const runEntry = (e: Entry) => {
    e.run();
    onClose();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, entries.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const sel = entries[clampedActive];
      if (sel) runEntry(sel);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  const sections: { key: Section; title: string; entries: Entry[] }[] = [
    { key: "navigate" as const, title: t("commandPalette.sectionNavigate"), entries: navEntries },
    { key: "actions" as const, title: t("commandPalette.sectionActions"), entries: actionEntries },
    { key: "sources" as const, title: t("commandPalette.sectionSources"), entries: sourceEntries },
    { key: "items" as const, title: t("commandPalette.sectionItems"), entries: itemEntries },
  ].filter((s) => s.entries.length > 0);

  // Running index shared with the flat `entries` array so the highlighted
  // row and the Enter target stay in lockstep.
  let flatIndex = 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 pt-[12vh] backdrop-blur-sm"
      onMouseDown={onClose}
      role="presentation"
    >
      <div
        className="w-full max-w-xl overflow-hidden rounded-xl border bg-popover text-popover-foreground shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={t("commandPalette.placeholder")}
        data-testid="command-palette"
      >
        <div className="flex items-center gap-2 border-b px-3">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t("commandPalette.placeholder")}
            className="h-11 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
            data-testid="command-palette-input"
            aria-label={t("commandPalette.placeholder")}
          />
        </div>

        <div ref={listRef} className="max-h-[50vh] overflow-y-auto p-2">
          {entries.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground" data-testid="command-palette-empty">
              {itemsFetching && debounced
                ? t("commandPalette.searching")
                : t("commandPalette.empty", { query: query.trim() })}
            </div>
          ) : (
            sections.map((section) => (
              <div key={section.key} className="mb-1 last:mb-0">
                <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {section.title}
                </div>
                {section.entries.map((entry) => {
                  const idx = flatIndex++;
                  const selected = idx === clampedActive;
                  return (
                    <button
                      key={entry.id}
                      type="button"
                      data-selected={selected}
                      data-command-item={entry.id}
                      onMouseMove={() => setActive(idx)}
                      onClick={() => runEntry(entry)}
                      className={cn(
                        "flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left text-sm transition-colors",
                        selected
                          ? "bg-accent text-accent-foreground"
                          : "text-foreground hover:bg-accent/50",
                      )}
                    >
                      <entry.icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <span className="flex-1 truncate">{entry.label}</span>
                      {entry.hint && (
                        <span className="shrink-0 truncate text-[11px] text-muted-foreground">
                          {entry.hint}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
