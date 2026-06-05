import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Library, Search } from "lucide-react";
import { usePrismStore } from "@/store";
import { useMemo } from "react";

export function KnowledgePage() {
  const { data: items, isLoading } = useQuery({ queryKey: ["items"], queryFn: () => api.listItems() });
  const searchQuery = usePrismStore((s) => s.searchQuery);

  const grouped = useMemo(() => {
    const map = new Map<string, typeof items>();
    (items ?? []).forEach((it) => {
      const key = it.sourceName;
      if (!map.has(key)) map.set(key, [] as typeof items);
      map.get(key)!.push(it);
    });
    return Array.from(map.entries());
  }, [items]);

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Knowledge Base</h2>
          <p className="text-sm text-muted-foreground">
            All distilled knowledge, grouped by source.
          </p>
        </div>
        <Badge variant="secondary" className="gap-1">
          <Library className="h-3 w-3" />
          {items?.length ?? 0} entries
        </Badge>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : grouped.length === 0 ? (
        <div className="flex h-64 items-center justify-center rounded-lg border border-dashed">
          <div className="text-center space-y-2">
            <Search className="mx-auto h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {searchQuery ? "No matches" : "No knowledge yet — add a source and run a sync."}
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          {grouped.map(([sourceName, list]) => (
            <section key={sourceName}>
              <h3 className="mb-2 text-sm font-semibold">{sourceName}</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {list?.map((item) => (
                  <Card key={item.id} className="hover:bg-accent/30">
                    <CardHeader className="pb-2">
                      <CardTitle className="line-clamp-2 text-sm">{item.title}</CardTitle>
                      <CardDescription className="text-xs">
                        {item.tags?.slice(0, 3).join(" · ")}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="pt-0">
                      <p className="line-clamp-3 text-xs text-muted-foreground">{item.summary}</p>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
