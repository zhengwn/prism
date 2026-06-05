import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { usePrismStore } from "@/store";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { formatRelativeTime, cn } from "@/lib/utils";
import { Inbox as InboxIcon, Star, CheckCircle2 } from "lucide-react";
import type { KnowledgeItem } from "@/types";

export function InboxPage() {
  const { data: items, isLoading } = useQuery({
    queryKey: ["items"],
    queryFn: () => api.listItems(),
  });

  const { data: sources } = useQuery({
    queryKey: ["sources"],
    queryFn: () => api.listSources(),
  });

  const selectedSourceId = usePrismStore((s) => s.selectedSourceId);
  const setSelectedSource = usePrismStore((s) => s.setSelectedSource);
  const selectedItemId = usePrismStore((s) => s.selectedItemId);
  const setSelectedItem = usePrismStore((s) => s.setSelectedItem);
  const searchQuery = usePrismStore((s) => s.searchQuery);
  const statusFilter = usePrismStore((s) => s.statusFilter);
  const setStatusFilter = usePrismStore((s) => s.setStatusFilter);

  const filteredItems: KnowledgeItem[] = (items ?? []).filter((it) => {
    if (selectedSourceId && it.sourceId !== selectedSourceId) return false;
    if (statusFilter === "unread" && it.status !== "unread") return false;
    if (statusFilter === "starred" && it.status !== "starred") return false;
    if (statusFilter === "archived" && it.status !== "archived") return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      if (!it.title.toLowerCase().includes(q) && !(it.summary?.toLowerCase().includes(q))) {
        return false;
      }
    }
    return true;
  });

  return (
    <div className="flex h-full">
      {/* Source filter rail */}
      <div className="hidden w-48 shrink-0 border-r bg-card/20 p-3 md:block">
        <h3 className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Filter
        </h3>
        <Button
          variant={selectedSourceId === null ? "secondary" : "ghost"}
          size="sm"
          className="w-full justify-start"
          onClick={() => setSelectedSource(null)}
        >
          All sources
        </Button>

        <h3 className="mt-4 mb-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Status
        </h3>
        <div className="space-y-1">
          {(["all", "unread", "starred", "archived"] as const).map((s) => (
            <Button
              key={s}
              variant={statusFilter === s ? "secondary" : "ghost"}
              size="sm"
              className="w-full justify-start capitalize"
              onClick={() => setStatusFilter(s)}
            >
              {s}
            </Button>
          ))}
        </div>

        <h3 className="mt-4 mb-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Sources
        </h3>
        <div className="space-y-1">
          {sources?.map((src) => (
            <Button
              key={src.id}
              variant={selectedSourceId === src.id ? "secondary" : "ghost"}
              size="sm"
              className="w-full justify-between"
              onClick={() => setSelectedSource(src.id)}
            >
              <span className="truncate">{src.name}</span>
              <span className="text-[10px] text-muted-foreground">{src.itemCount}</span>
            </Button>
          ))}
        </div>
      </div>

      {/* Items list */}
      <div className="flex-1 overflow-hidden">
        <div className="flex h-12 items-center justify-between border-b px-4">
          <div>
            <h2 className="text-sm font-semibold">Inbox</h2>
            <p className="text-xs text-muted-foreground">
              {filteredItems.length} item{filteredItems.length === 1 ? "" : "s"}
              {selectedSourceId ? " in selected source" : ""}
            </p>
          </div>
        </div>

        <ScrollArea className="h-[calc(100%-3rem)]">
          {isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-24 w-full" />
              ))}
            </div>
          ) : filteredItems.length === 0 ? (
            <EmptyState />
          ) : (
            <div className="space-y-2 p-4">
              {filteredItems.map((item) => (
                <ItemRow
                  key={item.id}
                  item={item}
                  selected={item.id === selectedItemId}
                  onClick={() => setSelectedItem(item.id)}
                />
              ))}
            </div>
          )}
        </ScrollArea>
      </div>
    </div>
  );
}

function ItemRow({ item, selected, onClick }: { item: KnowledgeItem; selected: boolean; onClick: () => void }) {
  return (
    <Card
      className={cn(
        "cursor-pointer transition-colors hover:bg-accent/40",
        selected && "border-primary/50 bg-accent/50",
      )}
      onClick={onClick}
    >
      <CardContent className="p-3">
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0 space-y-1">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
                {item.sourceName}
              </Badge>
              <span>·</span>
              <span>{formatRelativeTime(item.publishedAt)}</span>
              {item.status === "starred" && <Star className="h-3 w-3 fill-amber-400 text-amber-400" />}
              {item.status === "read" && <CheckCircle2 className="h-3 w-3 text-emerald-500" />}
            </div>
            <h3 className="text-sm font-medium leading-tight">{item.title}</h3>
            {item.summary && (
              <p className="line-clamp-2 text-xs text-muted-foreground">{item.summary}</p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full items-center justify-center p-12">
      <div className="max-w-sm text-center space-y-3">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl prism-gradient">
          <InboxIcon className="h-6 w-6 text-white" />
        </div>
        <h3 className="text-sm font-semibold">Your inbox is empty</h3>
        <p className="text-xs text-muted-foreground">
          Add a source to start collecting AI news. Prism will distill each item into searchable knowledge.
        </p>
      </div>
    </div>
  );
}
