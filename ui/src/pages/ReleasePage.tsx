import { Link, useParams } from "react-router"
import { Play, Shuffle } from "lucide-react"
import { useRelease, useReleaseTracks, useReleaseRelated, useReleaseRecommendations } from "@/api/hooks/useRelease"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import ArtworkImage from "@/components/media/ArtworkImage"
import LikeButton from "@/components/media/LikeButton"
import TrackTable from "@/components/media/TrackTable"
import Shelf from "@/components/media/Shelf"
import { usePlayerStore } from "@/store/playerStore"
import type { ReleaseSummary } from "@/api/types"

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return ""
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m} min`
}

function ReleasePageSkeleton() {
  return (
    <div className="space-y-8">
      <div className="px-4 sm:px-6 pt-8 flex gap-6 items-end">
        <Skeleton className="w-44 h-44 rounded-lg shrink-0" />
        <div className="space-y-3 pb-2 flex-1">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-4 w-28" />
          <div className="flex gap-2 mt-2">
            <Skeleton className="h-9 w-20" />
            <Skeleton className="h-9 w-20" />
          </div>
        </div>
      </div>
      <div className="px-4 sm:px-6 space-y-2">
        {[1, 2, 3, 4, 5, 6].map((i) => (
          <Skeleton key={i} className="h-12 w-full rounded-md" />
        ))}
      </div>
    </div>
  )
}

export default function ReleasePage() {
  const { id } = useParams<{ id: string }>()
  const releaseId = Number(id)
  const { data: releaseData, isLoading: relLoading, error } = useRelease(releaseId)
  const { data: tracksData, isLoading: tracksLoading } = useReleaseTracks(releaseId)
  const { data: relatedData } = useReleaseRelated(releaseId)
  const { data: recsData } = useReleaseRecommendations(releaseId)
  const playSource = usePlayerStore((s) => s.playSource)
  const patchSession = usePlayerStore((s) => s.toggleShuffle)
  const isLoading = relLoading || tracksLoading

  if (isLoading) return <ReleasePageSkeleton />
  if (error || !releaseData) {
    return (
      <div className="p-8">
        <p className="text-destructive text-sm">Release not found.</p>
      </div>
    )
  }

  const { release } = releaseData
  const tracks = tracksData?.items ?? []
  const related = (relatedData?.items ?? []) as ReleaseSummary[]

  return (
    <div className="relative pb-8">
      {/* Header */}
      <div className="relative space-y-8">
      <div className="px-4 sm:px-6 pt-8 pb-4 flex flex-col sm:flex-row gap-4 sm:gap-6 items-start sm:items-end">
        <ArtworkImage
          src={release.artwork?.url}
          alt={release.title}
          size={176}
          className="rounded-lg shrink-0"
          fallbackLetter={release.title[0]}
        />
        <div className="pb-0 sm:pb-2 min-w-0">
          <p className="text-xs text-muted-foreground uppercase tracking-wide mb-1">
            {release.release_type_label}
          </p>
          <h1 className="text-3xl font-bold truncate">{release.title}</h1>

          {/* Artists */}
          <div className="flex flex-wrap gap-1 mt-1.5 text-sm text-muted-foreground">
            {release.artists.map((a, i) => (
              <span key={a.id}>
                <Link to={`/artists/${a.id}`} className="hover:text-foreground hover:underline">
                  {a.name}
                </Link>
                {i < release.artists.length - 1 && <span>,</span>}
              </span>
            ))}
            {release.release_year && <span>· {release.release_year}</span>}
            {release.track_count > 0 && <span>· {release.track_count} tracks</span>}
            {release.duration && <span>· {formatDuration(release.duration)}</span>}
          </div>

          <div className="flex gap-2 mt-4 items-center">
            <Button
              size="sm"
              onClick={() => playSource("release", releaseId, release.title)}
              className="gap-2"
            >
              <Play size={14} fill="currentColor" strokeWidth={0} />
              Play
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={async () => {
                await playSource("release", releaseId, release.title)
                await patchSession()
              }}
              className="gap-2"
            >
              <Shuffle size={14} />
              Shuffle
            </Button>
            <LikeButton entity="album" id={releaseId} variant="control" size={18} />
          </div>
        </div>
      </div>

      {/* Tracks */}
      <div className="px-4 sm:px-6">
        <TrackTable
          tracks={tracks}
          showRelease={false}
          sourceLabel={release.title}
          releaseId={releaseId}
        />
      </div>

      {/* Related discography */}
      {related.length > 0 && (
        <Shelf
          title="More from these artists"
          items={related
            .filter((r) => r.id !== releaseId)
            .map((r) => ({
              id: r.id,
              type: "release" as const,
              title: r.title,
              subtitle: r.artists.map((a) => a.name).join(", "),
              artwork: r.artwork,
              onPlay: () => playSource("release", r.id, r.title),
            }))}
        />
      )}

      {/* Recommended albums */}
      {recsData?.available && recsData.items.length > 0 && (
        <Shelf
          title="Recommended Albums"
          items={recsData.items.map((item) => ({
            id: item.entity_id,
            type: "release" as const,
            title: item.title,
            subtitle: item.subtitle,
            subtitleLinks: item.subtitle_links,
            href: item.action?.target,
            reason: item.reason,
            artwork: item.artwork,
            onPlay: item.play_action
              ? () => playSource("release", Number(item.entity_id), item.title)
              : undefined,
          }))}
        />
      )}
      </div>{/* /z-10 */}
    </div>
  )
}
