import { Heart, History, Shuffle, Disc3, Plus, Compass, RotateCcw, Clock, Radio } from "lucide-react"
import Shelf from "./Shelf"
import { usePlayerStore } from "@/store/playerStore"
import { playLikes } from "@/api/playlists"
import { startFlow } from "@/api/flow"
import { useFlowProfile } from "@/api/hooks/useFlowProfile"
import type { MediaCardProps } from "./MediaCard"
import type { PlaybackEnvelope } from "@/api/types"

function cardArtwork(icon: React.ReactNode) {
  return (
    <div className="w-full aspect-square flex items-center justify-center">
      {icon}
    </div>
  )
}

export default function ForYouShelf() {
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  const { data: flowProfile } = useFlowProfile()
  const flowAvailable = flowProfile?.available === true

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
      id: "flow",
      type: "static",
      title: "Flow",
      subtitle: flowAvailable
        ? "Your personal stream"
        : flowProfile
          ? "Build your profile first"
          : "Loading…",
      artworkNode: cardArtwork(<Radio size={72} />),
      variant: "shelf",
      disabled: !flowAvailable,
      onPlay: flowAvailable ? () => { handleStartFlow().catch(() => {}) } : undefined,
    },
    {
      id: "likes",
      type: "playlist",
      title: "Liked Tracks",
      subtitle: "Your favourites",
      artworkNode: cardArtwork(<Heart size={72} />),
      onPlay: () => playLikes().then(playFromEnvelope).catch(() => {}),
      variant: "shelf",
    },
    {
      id: "history",
      type: "shelf",
      title: "Recently Played",
      subtitle: "Your listening history",
      artworkNode: cardArtwork(<History size={72} />),
      variant: "shelf",
    },
    {
      id: "mixes_for_you",
      type: "shelf",
      title: "Mixes For You",
      subtitle: "AI-generated mixes",
      artworkNode: cardArtwork(<Shuffle size={72} />),
      variant: "shelf",
    },
    {
      id: "new_releases",
      type: "shelf",
      title: "New Releases",
      subtitle: "Fresh from your library",
      artworkNode: cardArtwork(<Disc3 size={72} />),
      variant: "shelf",
    },
    {
      id: "recently_added",
      type: "shelf",
      title: "Recently Added",
      subtitle: "New to your collection",
      artworkNode: cardArtwork(<Plus size={72} />),
      variant: "shelf",
    },
    {
      id: "discover_random",
      type: "shelf",
      title: "Discover",
      subtitle: "Something new",
      artworkNode: cardArtwork(<Compass size={72} />),
      variant: "shelf",
    },
    {
      id: "listen_again",
      type: "shelf",
      title: "Listen Again",
      subtitle: "Pick up where you left off",
      artworkNode: cardArtwork(<RotateCcw size={72} />),
      variant: "shelf",
    },
    {
      id: "long_time_no_listen",
      type: "shelf",
      title: "Long Time No Listen",
      subtitle: "Forgotten gems",
      artworkNode: cardArtwork(<Clock size={72} />),
      variant: "shelf",
    },
  ]

  return <Shelf items={cards} />
}
