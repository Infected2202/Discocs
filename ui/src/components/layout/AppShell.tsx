import { Outlet } from "react-router"
import Sidebar from "./Sidebar"
import TopBar from "./TopBar"
import MobileTabBar from "./MobileTabBar"
import PlayerBar from "@/components/player/PlayerBar"
import ExpandedPlayer from "@/components/player/ExpandedPlayer"
import ErrorBoundary from "@/components/common/ErrorBoundary"
import PageTransition from "@/components/common/PageTransition"
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts"
import { useTrackTitle } from "@/hooks/useTrackTitle"

export default function AppShell() {
  useKeyboardShortcuts()
  useTrackTitle()

  return (
    <div className="flex flex-col h-svh overflow-hidden bg-background">
      {/* TopBar — desktop only */}
      <div className="hidden md:block shrink-0">
        <TopBar />
      </div>

      {/* Below: sidebar + content */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Sidebar — desktop only */}
        <div className="hidden md:block h-full">
          <Sidebar />
        </div>

        {/* Main column: scrollable content */}
        <main className="flex-1 overflow-y-auto pb-[92px] md:pb-[92px] max-md:pb-[148px]">
          <ErrorBoundary>
            <PageTransition>
              <Outlet />
            </PageTransition>
          </ErrorBoundary>
        </main>
      </div>

      {/* Mobile tab bar — above player */}
      <MobileTabBar />

      {/* Player — always mounted, never unmounts */}
      <PlayerBar />
      <ExpandedPlayer />
    </div>
  )
}
