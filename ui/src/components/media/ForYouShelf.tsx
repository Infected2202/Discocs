import { useState, useEffect } from "react"
import Shelf from "./Shelf"
import { usePlayerStore } from "@/store/playerStore"
import { playLikes } from "@/api/playlists"
import { startFlow } from "@/api/flow"
import { useFlowProfile } from "@/api/hooks/useFlowProfile"
import type { MediaCardProps } from "./MediaCard"
import type { PlaybackEnvelope } from "@/api/types"

function mixWithGray(hex: string, accentWeight = 0.3): string {
  const clean = hex.replace("#", "")
  const full = clean.length === 3
    ? clean.split("").map((c) => c + c).join("")
    : clean
  const r = parseInt(full.slice(0, 2), 16)
  const g = parseInt(full.slice(2, 4), 16)
  const b = parseInt(full.slice(4, 6), 16)
  const gr = 90 // gray target ~#5a5a5a
  const mr = Math.round(r * accentWeight + gr * (1 - accentWeight))
  const mg = Math.round(g * accentWeight + gr * (1 - accentWeight))
  const mb = Math.round(b * accentWeight + gr * (1 - accentWeight))
  return `rgb(${mr},${mg},${mb})`
}

function useAccentColor(): string {
  const [accent, setAccent] = useState(
    () => document.documentElement.dataset.trackAccentColor ?? "#3b6bff"
  )
  useEffect(() => {
    const handler = (e: Event) => {
      const a = (e as CustomEvent<{ accent?: string }>).detail?.accent
      if (a) setAccent(a)
    }
    window.addEventListener("trackaccentchange", handler)
    return () => window.removeEventListener("trackaccentchange", handler)
  }, [])
  return accent
}

function cardArtwork(bg: string, icon: React.ReactNode) {
  return (
    <div
      className="w-full aspect-square rounded-full flex items-center justify-center"
      style={{ background: bg }}
    >
      <div className="w-[55%] h-[55%] flex items-center justify-center opacity-90">
        {icon}
      </div>
    </div>
  )
}

const ICONS = {
  likes: (
    <svg viewBox="0 0 24 24" fill="white" width="100%" height="100%">
      <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/>
      <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/>
    </svg>
  ),
  mixes: (
    <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" width="100%" height="100%">
      <circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>
      <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
    </svg>
  ),
  newReleases: (
    <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" width="100%" height="100%">
      <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
    </svg>
  ),
  recentlyAdded: (
    <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" width="100%" height="100%">
      <path d="M12 5v14"/><path d="M5 12h14"/>
    </svg>
  ),
  discover: (
    <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" width="100%" height="100%">
      <polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/>
      <polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/>
    </svg>
  ),
  listenAgain: (
    <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" width="100%" height="100%">
      <polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.85"/>
    </svg>
  ),
  longTime: (
    <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" width="100%" height="100%">
      <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
    </svg>
  ),
  flow: (
    <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" width="100%" height="100%">
      <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
    </svg>
  ),
}

export default function ForYouShelf() {
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  const { data: flowProfile } = useFlowProfile()
  const flowAvailable = flowProfile?.available === true
  const accent = useAccentColor()
  const bg = mixWithGray(accent)

  async function handleStartFlow() {
    const resp = await startFlow()
    const envelope: PlaybackEnvelope = {
      session: resp.session,
      queue: { ...resp.queue, current_item: resp.queue.items[0] ?? null },
    }
    await playFromEnvelope(envelope)
  }

  const cards: MediaCardProps[] = [
    {
      id: "likes",
      type: "playlist",
      title: "Liked Tracks",
      subtitle: "Your favourites",
      artworkNode: cardArtwork(bg, ICONS.likes),
      onPlay: () => playLikes().then(playFromEnvelope).catch(() => {}),
      variant: "shelf",
    },
    {
      id: "history",
      type: "shelf",
      title: "Recently Played",
      subtitle: "Your listening history",
      artworkNode: cardArtwork(bg, ICONS.listenAgain),
      variant: "shelf",
    },
    {
      id: "mixes_for_you",
      type: "shelf",
      title: "Mixes For You",
      subtitle: "AI-generated mixes",
      artworkNode: cardArtwork(bg, ICONS.mixes),
      variant: "shelf",
    },
    {
      id: "new_releases",
      type: "shelf",
      title: "New Releases",
      subtitle: "Fresh from your library",
      artworkNode: cardArtwork(bg, ICONS.newReleases),
      variant: "shelf",
    },
    {
      id: "recently_added",
      type: "shelf",
      title: "Recently Added",
      subtitle: "New to your collection",
      artworkNode: cardArtwork(bg, ICONS.recentlyAdded),
      variant: "shelf",
    },
    {
      id: "discover_random",
      type: "shelf",
      title: "Discover",
      subtitle: "Something new",
      artworkNode: cardArtwork(bg, ICONS.discover),
      variant: "shelf",
    },
    {
      id: "listen_again",
      type: "shelf",
      title: "Listen Again",
      subtitle: "Pick up where you left off",
      artworkNode: cardArtwork(bg, ICONS.listenAgain),
      variant: "shelf",
    },
    {
      id: "long_time_no_listen",
      type: "shelf",
      title: "Long Time No Listen",
      subtitle: "Forgotten gems",
      artworkNode: cardArtwork(bg, ICONS.longTime),
      variant: "shelf",
    },
    {
      id: "flow",
      type: "static",
      title: "Flow",
      subtitle: flowAvailable
        ? "Your personal stream"
        : flowProfile
          ? "Build your profile first"
          : "Loading…",
      artworkNode: cardArtwork(bg, ICONS.flow),
      variant: "shelf",
      disabled: !flowAvailable,
      onPlay: flowAvailable ? () => { handleStartFlow().catch(() => {}) } : undefined,
    },
  ]

  return <Shelf title="For You" items={cards} />
}
