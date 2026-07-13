import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { DetailPanel } from "./DetailPanel";
import { CommandPalette } from "./CommandPalette";
import { useSyncNotifications } from "@/hooks/useSyncNotifications";

export function AppLayout() {
  // Background-sync → OS notification (opt-in, polls while enabled).
  useSyncNotifications();
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar />
        <main className="flex-1 overflow-hidden">
          <Outlet />
        </main>
      </div>
      <DetailPanel />
      {/* Global ⌘K overlay — mounted once, renders its own shortcut listener. */}
      <CommandPalette />
    </div>
  );
}
