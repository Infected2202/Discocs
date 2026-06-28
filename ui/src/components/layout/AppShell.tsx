import { Outlet } from "react-router"
import Sidebar from "./Sidebar"
import TopBar from "./TopBar"
import PlayerBar from "@/components/player/PlayerBar"
import ExpandedPlayer from "@/components/player/ExpandedPlayer"

export default function AppShell() {
  return (
    <div className="flex h-svh overflow-hidden bg-background">
      {/* Sidebar — fixed left column */}
      <Sidebar />

      {/* Main column: topbar + scrollable content */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <TopBar />

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
