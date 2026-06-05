import { NavLink, useLocation } from "react-router-dom";
import {
  Inbox,
  Library,
  Rss,
  Sparkles,
  Settings,
  CircleDot,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

interface NavItem {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string | number;
}

const primaryNav: NavItem[] = [
  { to: "/inbox", label: "Inbox", icon: Inbox },
  { to: "/knowledge", label: "Knowledge", icon: Library },
  { to: "/sources", label: "Sources", icon: Rss },
];

const secondaryNav: NavItem[] = [
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const location = useLocation();

  return (
    <aside className="flex h-full w-56 shrink-0 flex-col border-r bg-card/30">
      {/* Brand */}
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <div className="flex h-7 w-7 items-center justify-center rounded-md prism-gradient">
          <Sparkles className="h-4 w-4 text-white" />
        </div>
        <div className="flex flex-col leading-tight">
          <span className="text-sm font-semibold">Prism</span>
          <span className="text-[10px] text-muted-foreground">Refract the noise</span>
        </div>
      </div>

      {/* Primary nav */}
      <nav className="flex-1 space-y-1 overflow-y-auto p-2">
        <div className="px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Workspace
        </div>
        {primaryNav.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                isActive
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
              )
            }
          >
            <item.icon className="h-4 w-4" />
            <span className="flex-1">{item.label}</span>
            {item.badge !== undefined && (
              <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
                {item.badge}
              </Badge>
            )}
          </NavLink>
        ))}

        <div className="px-2 pt-4 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          System
        </div>
        {secondaryNav.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                isActive
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
              )
            }
          >
            <item.icon className="h-4 w-4" />
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer / status */}
      <div className="border-t p-3 text-[11px] text-muted-foreground">
        <div className="flex items-center gap-1.5">
          <CircleDot className="h-3 w-3 text-emerald-500" />
          <span>Sidecar connected</span>
        </div>
        <div className="mt-1 truncate opacity-70">
          v0.1.0 · {location.pathname}
        </div>
      </div>
    </aside>
  );
}
