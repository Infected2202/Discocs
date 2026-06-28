import { Outlet } from "react-router"
import Sidebar from "./Sidebar"
import TopBar from "./TopBar"
import PlayerBar from "@/components/player/PlayerBar"
import ExpandedPlayer from "@/components/player/ExpandedPlayer"

export default function AppShell() {
  return (
    <div className="flex flex-col h-svh overflow-hidden bg-background">
      {/* TopBar — full width */}
      <TopBar />

      {/* Below: sidebar + content */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <Sidebar />

        {/* Main column: scrollable content */}
        <main className="flex-1 overflow-y-auto pb-[92px]">
          <Outlet />
        </main>
      </div>

      {/* Player — always mounted, never unmounts */}
      <PlayerBar />
      <ExpandedPlayer />
    </div>
  )
}
