import { useState } from "react"
import { useParams, useNavigate } from "react-router"
import { Play, ChevronLeft, Pencil, Trash2, X } from "lucide-react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  fetchLikesPlaylist,
  playLikes,
  fetchPlaylist,
  playPlaylist,
  deletePlaylist,
  removePlaylistTracks,
  reorderPlaylistTracks,
  type LikesPlaylist,
} from "@/api/playlists"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import ArtworkImage from "@/components/media/ArtworkImage"
import VirtualTrackList from "@/components/media/VirtualTrackList"
import { usePlayerStore } from "@/store/playerStore"
import { useUIStore } from "@/store/uiStore"
import type { PlaylistDetail, TrackSummary } from "@/api/types"

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
  const queryClient = useQueryClient()
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  const openCreatePlaylist = useUIStore((s) => s.openCreatePlaylist)

  const isLikes = id === "likes"
  const playlistId = isLikes ? null : Number(id)
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<number>>(new Set())

  const { data, isLoading, error } = useQuery<LikesPlaylist | PlaylistDetail>({
    queryKey: ["playlist", isLikes ? "likes" : playlistId],
    queryFn: (): Promise<LikesPlaylist | PlaylistDetail> =>
      isLikes ? fetchLikesPlaylist() : fetchPlaylist(playlistId!),
    enabled: isLikes || Number.isInteger(playlistId),
    staleTime: 30_000,
  })

  const { mutate: removeSelected, isPending: removing } = useMutation({
    mutationFn: (trackIds: number[]) => removePlaylistTracks(playlistId!, trackIds),
    onSuccess: () => {
      setSelectedIds(new Set())
      queryClient.invalidateQueries({ queryKey: ["playlist", playlistId] })
      queryClient.invalidateQueries({ queryKey: ["playlists"] })
    },
  })

  const { mutate: reorder } = useMutation({
    mutationFn: (trackIds: number[]) => reorderPlaylistTracks(playlistId!, trackIds),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["playlist", playlistId] })
      queryClient.invalidateQueries({ queryKey: ["playlists"] })
    },
  })

  const { mutate: handleDelete, isPending: deleting } = useMutation({
    mutationFn: () => deletePlaylist(playlistId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["playlists"] })
      navigate("/")
    },
  })

  if (isLoading) return <PlaylistSkeleton />
  if (error || !data) {
    return (
      <div className="p-8">
        <p className="text-destructive text-sm">Playlist not found.</p>
      </div>
    )
  }

  const detail = isLikes ? null : (data as PlaylistDetail)
  const tracks = data.tracks as TrackSummary[]

  function toggleSelect(trackId: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(trackId)) next.delete(trackId)
      else next.add(trackId)
      return next
    })
  }

  // Starts the whole playlist. With a trackId, playback begins at that track
  // (row click); without one, at the top (the header Play button).
  function playPlaylistFrom(trackId?: number) {
    const play = isLikes ? playLikes() : playPlaylist(playlistId!)
    play.then((envelope) => playFromEnvelope(envelope, trackId)).catch(() => {})
  }

  function handleEdit() {
    if (detail) openCreatePlaylist({ playlist: detail })
  }

  function confirmDelete() {
    if (globalThis.confirm(`Delete playlist "${data!.title}"?`)) handleDelete()
  }

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
          {isLikes ? (
            <LikesArtwork />
          ) : (
            <ArtworkImage
              src={detail?.artwork?.url}
              alt={data.title}
              size={176}
              className="rounded-lg shrink-0"
              fallbackLetter="P"
            />
          )}

          <div className="pb-0 sm:pb-2 min-w-0">
            <p className="text-xs text-muted-foreground uppercase tracking-wide mb-1">Playlist</p>
            <h1 className="text-3xl font-bold">{data.title}</h1>
            {detail?.description && (
              <p className="text-sm text-muted-foreground mt-1">{detail.description}</p>
            )}
            <p className="text-sm text-muted-foreground mt-1">{tracks.length} tracks</p>
            <div className="mt-4 flex gap-2">
              <Button size="sm" onClick={() => playPlaylistFrom()} className="gap-2">
                <Play size={14} fill="currentColor" strokeWidth={0} />
                Play
              </Button>
              {!isLikes && (
                <>
                  <Button size="sm" variant="outline" onClick={handleEdit} className="gap-2">
                    <Pencil size={14} />
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={confirmDelete}
                    disabled={deleting}
                    className="gap-2 text-destructive hover:text-destructive"
                  >
                    <Trash2 size={14} />
                    Delete
                  </Button>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Selection bar */}
      {!isLikes && selectedIds.size > 0 && (
        <div className="px-4 sm:px-6">
          <div className="flex items-center gap-3 rounded-md bg-muted/60 px-4 py-2 text-sm">
            <span>{selectedIds.size} selected</span>
            <button
              onClick={() => removeSelected([...selectedIds])}
              disabled={removing}
              className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:text-destructive transition-colors disabled:opacity-50"
              aria-label="Remove selected tracks"
            >
              <Trash2 size={15} />
            </button>
            <button
              onClick={() => setSelectedIds(new Set())}
              className="ml-auto flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors"
            >
              <X size={14} />
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Tracks */}
      <div className="px-4 sm:px-6">
        <VirtualTrackList
          tracks={tracks}
          sourceLabel={data.title}
          selectable={!isLikes}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onReorder={isLikes ? undefined : (trackIds) => reorder(trackIds)}
          onPlayTrack={(trackId) => playPlaylistFrom(trackId)}
        />
      </div>
    </div>
  )
}
