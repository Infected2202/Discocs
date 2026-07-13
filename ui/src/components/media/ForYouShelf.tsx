import { useTranslation } from "react-i18next"
import type { TFunction } from "i18next"
import { Heart, History, Shuffle, Disc3, Plus, Compass, RotateCcw, Clock, Radio, ListMusic } from "lucide-react"
import Shelf from "./Shelf"
import { usePlayerStore } from "@/store/playerStore"
import { playLikes } from "@/api/playlists"
import { startFlow } from "@/api/flow"
import { useFlowProfile } from "@/api/hooks/useFlowProfile"
import type { MediaCardProps } from "./MediaCard"
import type { PlaybackEnvelope } from "@/api/types"

function cardArtwork(icon: React.ReactNode) {
  return (
    <div className="flex aspect-square w-full items-center justify-center">
      {icon}
    </div>
  )
}

function flowSubtitle(
  flowAvailable: boolean,
  flowProfile: { available?: boolean } | undefined,
  t: TFunction<"dashboard">,
) {
  if (flowAvailable) {
    return t("cards.flow.subtitlePersonal")
  }
  if (flowProfile) {
    return t("cards.flow.subtitleBuildProfile")
  }
  return t("cards.flow.subtitleLoading")
}

function buildFlowEnvelope(resp: Awaited<ReturnType<typeof startFlow>>): PlaybackEnvelope {
  const items = resp.queue.items

  return {
    session: resp.session,
    queue: {
      items,
      current_index: 0,
      current_item: items[0] ?? null,
      upcoming: items.slice(1),
      played: [],
      source_items: items,
      generated_items: [],
      autoplay_pool: [],
    },
  }
}

export default function ForYouShelf() {
  const { t } = useTranslation("dashboard")
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  const { data: flowProfile } = useFlowProfile()
  const flowAvailable = flowProfile?.available === true

  async function handleStartFlow() {
    const resp = await startFlow()
    await playFromEnvelope(buildFlowEnvelope(resp))
  }

  const flowPlayHandler = flowAvailable
    ? () => {
        handleStartFlow().catch(() => {})
      }
    : undefined

  const cards: MediaCardProps[] = [
    {
      id: "flow",
      type: "static",
      title: t("cards.flow.title"),
      subtitle: flowSubtitle(flowAvailable, flowProfile, t),
      artworkNode: cardArtwork(<Radio size={72} />),
      variant: "shelf",
      disabled: !flowAvailable,
      onPlay: flowPlayHandler,
    },
    {
      id: "likes",
      type: "playlist",
      title: t("cards.likedTracks.title"),
      subtitle: t("cards.likedTracks.subtitle"),
      artworkNode: cardArtwork(<Heart size={72} />),
      onPlay: () => playLikes().then(playFromEnvelope).catch(() => {}),
      variant: "shelf",
    },
    {
      id: "playlists",
      type: "shelf",
      title: t("cards.playlists.title"),
      subtitle: t("cards.playlists.subtitle"),
      artworkNode: cardArtwork(<ListMusic size={72} />),
      variant: "shelf",
    },
    {
      id: "history",
      type: "shelf",
      title: t("cards.history.title"),
      subtitle: t("cards.history.subtitle"),
      artworkNode: cardArtwork(<History size={72} />),
      variant: "shelf",
    },
    {
      id: "mixes_for_you",
      type: "shelf",
      title: t("cards.mixesForYou.title"),
      subtitle: t("cards.mixesForYou.subtitle"),
      artworkNode: cardArtwork(<Shuffle size={72} />),
      variant: "shelf",
    },
    {
      id: "new_releases",
      type: "shelf",
      title: t("cards.newReleases.title"),
      subtitle: t("cards.newReleases.subtitle"),
      artworkNode: cardArtwork(<Disc3 size={72} />),
      variant: "shelf",
    },
    {
      id: "recently_added",
      type: "shelf",
      title: t("cards.recentlyAdded.title"),
      subtitle: t("cards.recentlyAdded.subtitle"),
      artworkNode: cardArtwork(<Plus size={72} />),
      variant: "shelf",
    },
    {
      id: "discover_random",
      type: "shelf",
      title: t("cards.discover.title"),
      subtitle: t("cards.discover.subtitle"),
      artworkNode: cardArtwork(<Compass size={72} />),
      variant: "shelf",
    },
    {
      id: "listen_again",
      type: "shelf",
      title: t("cards.listenAgain.title"),
      subtitle: t("cards.listenAgain.subtitle"),
      artworkNode: cardArtwork(<RotateCcw size={72} />),
      variant: "shelf",
    },
    {
      id: "long_time_no_listen",
      type: "shelf",
      title: t("cards.longTimeNoListen.title"),
      subtitle: t("cards.longTimeNoListen.subtitle"),
      artworkNode: cardArtwork(<Clock size={72} />),
      variant: "shelf",
    },
  ]

  return <Shelf items={cards} />
}
