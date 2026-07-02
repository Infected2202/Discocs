import { useParams, useNavigate } from "react-router"
import { Play, ChevronLeft } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { fetchLikesPlaylist, playLikes } from "@/api/playlists"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import VirtualTrackList from "@/components/media/VirtualTrackList"
import { usePlayerStore } from "@/store/playerStore"
import type { TrackSummary } from "@/api/types"

function PlaylistSkeleton() {
  return (
    <div className="space-y-8">
      <div className="px-4 sm:px-6 pt-8 flex gap-6 items-end">
        <Skeleton className="w-44 h-44 rounded-lg shrink-0" />
        <div className="space-y-3 pb-2">
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-9 w-20 mt-2" />
        </div>
      </div>
      <div className="px-4 sm:px-6 space-y-2">
        {[1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-12 w-full rounded-md" />
        ))}
      </div>
    </div>
  )
}

// Gradient artwork for the likes playlist
function LikesArtwork() {
  return (
    <div className="w-44 h-44 rounded-lg shrink-0 flex items-center justify-center"
      style={{ background: "linear-gradient(135deg, #e91e8c 0%, #c2185b 50%, #880e4f 100%)" }}>
      <svg width="72" height="72" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg">
        <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/>
        <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/>
      </svg>
    </div>
  )
}

export default function PlaylistPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)

  const { data, isLoading, error } = useQuery({
    queryKey: ["playlist", id],
    queryFn: () => {
      if (id === "likes") return fetchLikesPlaylist()
      throw new Error("Unknown playlist")
    },
    staleTime: 30_000,
  })

  if (isLoading) return <PlaylistSkeleton />
  if (error || !data) {
    return (
      <div className="p-8">
        <p className="text-destructive text-sm">Playlist not found.</p>
      </div>
    )
  }

  const tracks = data.tracks as TrackSummary[]

  return (
    <div className="space-y-8 pb-8">
      {/* Header */}
      <div className="px-4 sm:px-6 pt-6 space-y-6">
        <button
          onClick={() => navigate(-1)}
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronLeft size={16} />
          Back
        </button>

        <div className="flex flex-col sm:flex-row gap-4 sm:gap-6 items-start sm:items-end">
          {id === "likes" && <LikesArtwork />}

          <div className="pb-0 sm:pb-2 min-w-0">
            <p className="text-xs text-muted-foreground uppercase tracking-wide mb-1">Playlist</p>
            <h1 className="text-3xl font-bold">{data.title}</h1>
            <p className="text-sm text-muted-foreground mt-1">{tracks.length} tracks</p>
            <div className="mt-4">
              <Button
                size="sm"
                onClick={() => playLikes().then(playFromEnvelope)}
                className="gap-2"
              >
                <Play size={14} fill="currentColor" strokeWidth={0} />
                Play
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Tracks */}
      <div className="px-4 sm:px-6">
        <VirtualTrackList tracks={tracks} sourceLabel={data.title} />
      </div>
    </div>
  )
}
