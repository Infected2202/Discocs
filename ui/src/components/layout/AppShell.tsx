import { useEffect, useRef, useState } from "react"
import { Outlet } from "react-router"
import Sidebar from "./Sidebar"
import AppHeader from "./AppHeader"
import PlayerBar from "@/components/player/PlayerBar"
import ExpandedPlayer from "@/components/player/ExpandedPlayer"
import PlaylistDialogs from "@/components/playlists/PlaylistDialogs"
import ErrorBoundary from "@/components/common/ErrorBoundary"
import PageTransition from "@/components/common/PageTransition"
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts"
import { useTrackTitle } from "@/hooks/useTrackTitle"
import { useArtworkTheme } from "@/hooks/useArtworkTheme"
import { useNavidromeStore } from "@/store/navidromeStore"
import { usePlayerStore } from "@/store/playerStore"
import PlasmaFBM from "@/components/player/PlasmaFBM"
import { ScrollContext } from "@/contexts/ScrollContext"

export default function AppShell() {
  useKeyboardShortcuts()
  useTrackTitle()
  useArtworkTheme()

  const mainRef = useRef<HTMLElement>(null)

  const fetchLikedIds = useNavidromeStore((s) => s.fetchLikedIds)
  useEffect(() => { fetchLikedIds() }, [fetchLikedIds])

  const restoreSession = usePlayerStore((s) => s.restoreSession)
  useEffect(() => { void restoreSession() }, [restoreSession])

  const [plasmaAccent, setPlasmaAccent] = useState(
    () => document.documentElement.dataset.trackAccentColor ?? "#3b6bff"
  )
  useEffect(() => {
    const handler = (e: Event) => {
      const accent = (e as CustomEvent<{ accent?: string }>).detail?.accent
      if (accent) setPlasmaAccent(accent)
    }
    globalThis.addEventListener("trackaccentchange", handler)
    return () => globalThis.removeEventListener("trackaccentchange", handler)
  }, [])


  return (
    <ScrollContext.Provider value={mainRef}>
      <div className="flex flex-col h-svh overflow-hidden">
        {/* Plasma */}
        <div className="fixed inset-0 pointer-events-none z-0" style={{ filter: "blur(4px)" }}>
          <PlasmaFBM active accent={plasmaAccent} speed={0.9} scale={1.0} opacity={0.9} />
        </div>

        {/* UI layers — above plasma */}
        <div className="relative z-[1] flex flex-col flex-1 min-h-0 overflow-hidden">
          {/* Sidebar + right column */}
          <div className="flex flex-1 min-h-0 overflow-hidden">
            {/* Sidebar — desktop only */}
            <div className="hidden md:block h-full">
              <Sidebar />
            </div>

            {/* Right: scrollable content with header inside */}
            <main ref={mainRef} className="flex-1 min-w-0 overflow-y-auto pb-[92px]">
              <AppHeader />
              <ErrorBoundary>
                <PageTransition>
                  <Outlet />
                </PageTransition>
              </ErrorBoundary>
            </main>
          </div>

          {/* Player — always mounted, never unmounts */}
          <PlayerBar />
          <ExpandedPlayer />
          <PlaylistDialogs />
        </div>
      </div>
    </ScrollContext.Provider>
  )
}
