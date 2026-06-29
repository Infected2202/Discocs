import { useParams } from "react-router"
import { Play, Heart } from "lucide-react"
import { useArtist, useArtistDiscography } from "@/api/hooks/useArtist"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import ArtworkImage from "@/components/media/ArtworkImage"
import Shelf from "@/components/media/Shelf"
import { usePlayerStore } from "@/store/playerStore"
import { useNavidromeStore } from "@/store/navidromeStore"
import { cn } from "@/lib/utils"
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
  const playSource = usePlayerStore((s) => s.playSource)
  const toggleArtistLike = useNavidromeStore((s) => s.toggleArtistLike)
  const liked = useNavidromeStore((s) => s.likedArtistIds.has(artistId))

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

  const bgArtwork = discoData?.groups
    .flatMap((g) => g.items as ReleaseSummary[])
    .find((r) => r.artwork?.url)?.artwork?.url

  return (
    <div className="relative pb-8">
      {/* Decorative background */}
      {bgArtwork && (
        <div className="absolute inset-x-0 top-0 h-[440px] overflow-hidden pointer-events-none">
          <img
            src={bgArtwork}
            aria-hidden
            className="w-full h-full object-cover object-bottom"
            style={{ filter: "blur(15px) brightness(0.4) saturate(1.3)", transform: "scale(1.15)", opacity: 0.5 }}
          />
          <div className="absolute inset-0" style={{ background: "linear-gradient(in oklab to bottom, rgb(11 13 15 / 0) 25%, rgb(11 13 15 / 0.5) 50%, rgb(11 13 15 / 0.85) 68%, rgb(11 13 15) 82%)" }} />
        </div>
      )}

      {/* Header */}
      <div className="relative z-10 space-y-8">
      <div className="px-4 sm:px-6 pt-8 pb-4 flex flex-col sm:flex-row gap-4 sm:gap-6 items-start sm:items-end">
        <ArtworkImage
          src={artist.image?.url}
          alt={artist.name}
          size={144}
          className="rounded-full shrink-0"
          fallbackLetter={artist.name[0]}
        />
        <div className="pb-0 sm:pb-2 min-w-0">
          <h1 className="text-3xl font-bold truncate">{artist.name}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {stats.tracks.toLocaleString()} tracks
            {stats.releases > 0 && ` · ${stats.releases.toLocaleString()} releases`}
            {stats.plays > 0 && ` · ${stats.plays.toLocaleString()} plays`}
          </p>
          <div className="flex gap-2 mt-4 items-center">
            <Button
              size="sm"
              onClick={() => playSource("artist", artistId, artist.name)}
              className="gap-2"
            >
              <Play size={14} fill="currentColor" strokeWidth={0} />
              Play
            </Button>
            <button
              onClick={() => toggleArtistLike(artistId)}
              title={liked ? "Unlike artist" : "Like artist"}
              className={cn(
                "p-1.5 rounded transition-colors",
                liked ? "text-primary" : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Heart size={18} fill={liked ? "currentColor" : "none"} />
            </button>
          </div>
        </div>
      </div>

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
