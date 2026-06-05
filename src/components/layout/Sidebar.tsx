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
import { useLanguage } from "@/hooks/useLanguage";

interface NavItem {
  to: string;
  i18nKey: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string | number;
}

export function Sidebar() {
  const location = useLocation();
  const { t } = useLanguage();

  // Defined inside the component so t() is in scope. The list is still
  // constant per render — no perf concern.
  const primaryNav: NavItem[] = [
    { to: "/inbox", i18nKey: "nav.inbox", icon: Inbox },
    { to: "/knowledge", i18nKey: "nav.knowledge", icon: Library },
    { to: "/sources", i18nKey: "nav.sources", icon: Rss },
  ];
  const secondaryNav: NavItem[] = [
    { to: "/settings", i18nKey: "nav.settings", icon: Settings },
  ];

  return (
    <aside className="flex h-full w-56 shrink-0 flex-col border-r bg-card/30">
      {/* Brand */}
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <div className="flex h-7 w-7 items-center justify-center rounded-md prism-gradient">
          <Sparkles className="h-4 w-4 text-white" />
        </div>
        <div className="flex flex-col leading-tight">
          <span className="text-sm font-semibold">{t("common.appName")}</span>
          <span className="text-[10px] text-muted-foreground">{t("common.appTagline")}</span>
        </div>
      </div>

      {/* Primary nav */}
      <nav className="flex-1 space-y-1 overflow-y-auto p-2">
        <div className="px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t("nav.workspace")}
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
            <span className="flex-1">{t(item.i18nKey)}</span>
            {item.badge !== undefined && (
              <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
                {item.badge}
              </Badge>
            )}
          </NavLink>
        ))}

        <div className="px-2 pt-4 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t("nav.system")}
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
            <span>{t(item.i18nKey)}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer / status */}
      <div className="border-t p-3 text-[11px] text-muted-foreground">
        <div className="flex items-center gap-1.5">
          <CircleDot className="h-3 w-3 text-emerald-500" />
          <span>{t("sidebar.sidecarConnected")}</span>
        </div>
        <div className="mt-1 truncate opacity-70">
          v0.1.0 · {location.pathname}
        </div>
      </div>
    </aside>
  );
}
