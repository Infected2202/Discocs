import { useParams } from "react-router"
import { Play, Shuffle } from "lucide-react"
import { useArtist, useArtistDiscography } from "@/api/hooks/useArtist"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import ArtworkImage from "@/components/media/ArtworkImage"
import CollectionHeader from "@/components/media/CollectionHeader"
import LikeButton from "@/components/media/LikeButton"
import Shelf from "@/components/media/Shelf"
import PopularTracks from "@/components/media/PopularTracks"
import { usePlayerStore } from "@/store/playerStore"
import type { ReleaseSummary } from "@/api/types"

function releaseSummaryToCard(r: ReleaseSummary, onPlay: () => void) {
  return {
    id: r.id,
    type: "release" as const,
    title: r.title,
    subtitle: r.release_year ? String(r.release_year) : null,
    artwork: r.artwork,
    onPlay,
  }
}

function ArtistPageSkeleton() {
  return (
    <div className="space-y-8">
      <div className="px-4 sm:px-6 pt-8 flex gap-6 items-end">
        <Skeleton className="w-36 h-36 rounded-full shrink-0" />
        <div className="space-y-3 pb-2">
          <Skeleton className="h-9 w-64" />
          <Skeleton className="h-4 w-40" />
        </div>
      </div>
      <div className="px-4 sm:px-6 space-y-3">
        {[1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-12 w-full rounded-md" />
        ))}
      </div>
    </div>
  )
}

export default function ArtistPage() {
  const { id } = useParams<{ id: string }>()
  const artistId = Number(id)
  const { data: artistData, isLoading: artistLoading, error } = useArtist(artistId)
  const { data: discoData, isLoading: discoLoading } = useArtistDiscography(artistId)
  const popularTracks = (() => {
    const items = artistData?.top_tracks ?? []
    const hasPlays = items.some((t) => t.play_count > 0)
    return hasPlays ? items.filter((t) => t.play_count > 0) : items.slice(0, 5)
  })()
  const playSource = usePlayerStore((s) => s.playSource)
  const toggleShuffle = usePlayerStore((s) => s.toggleShuffle)
  const isLoading = artistLoading || discoLoading

  if (isLoading) return <ArtistPageSkeleton />
  if (error || !artistData) {
    return (
      <div className="p-8">
        <p className="text-destructive text-sm">Artist not found.</p>
      </div>
    )
  }

  const { artist } = artistData
  const stats = artist.library_stats

  return (
    <div className="relative pb-8">
      {/* Header */}
      <div className="relative z-10 space-y-2">
      <CollectionHeader
        artwork={
          <ArtworkImage
            src={artist.image?.url}
            alt={artist.name}
            size={144}
            className="rounded-full shrink-0"
            fallbackLetter={artist.name[0]}
          />
        }
        title={artist.name}
        meta={
          <>
            {stats.tracks.toLocaleString()} tracks
            {stats.releases > 0 && ` · ${stats.releases.toLocaleString()} releases`}
            {stats.plays > 0 && ` · ${stats.plays.toLocaleString()} plays`}
          </>
        }
        actions={
          <>
            <Button
              size="sm"
              onClick={() => playSource("artist", artistId, artist.name)}
              className="gap-2"
            >
              <Play size={14} fill="currentColor" strokeWidth={0} />
              Play
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={async () => {
                await playSource("artist", artistId, artist.name)
                await toggleShuffle()
              }}
              className="gap-2"
            >
              <Shuffle size={14} />
              Shuffle
            </Button>
            <LikeButton entity="artist" id={artistId} variant="control" size={18} />
          </>
        }
      />

      {/* Popular tracks */}
      {popularTracks.length > 0 && (
        <PopularTracks tracks={popularTracks} sourceLabel={artist.name} />
      )}

      {/* Discography */}
      {discoData?.groups.map((group) => {
        if (group.items.length === 0) return null
        return (
          <Shelf
            key={group.key}
            title={group.title}
            items={(group.items as ReleaseSummary[]).map((r) =>
              releaseSummaryToCard(r, () => playSource("release", r.id, r.title))
            )}
          />
        )
      })}
      </div>{/* /z-10 */}
    </div>
  )
}
