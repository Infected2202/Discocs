import { useState } from "react"
import { useParams, useNavigate } from "react-router"
import { useTranslation } from "react-i18next"
import { Play, ChevronLeft, ListPlus, Pencil, Shuffle, Trash2, X, FolderInput } from "lucide-react"
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
import { ApiError } from "@/api/client"
import { isNetworkError } from "@/lib/apiErrorKind"
import { Button } from "@/components/ui/button"
import DownloadMenu from "@/components/common/DownloadMenu"
import { Skeleton } from "@/components/ui/skeleton"
import ArtworkImage from "@/components/media/ArtworkImage"
import CollectionHeader from "@/components/media/CollectionHeader"
import VirtualTrackList from "@/components/media/VirtualTrackList"
import ConfirmDialog from "@/components/common/ConfirmDialog"
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
  const { t } = useTranslation("playlist")
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  const openCreatePlaylist = useUIStore((s) => s.openCreatePlaylist)
  const openAddToPlaylist = useUIStore((s) => s.openAddToPlaylist)

  const isLikes = id === "likes"
  const playlistId = isLikes ? null : Number(id)
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<number>>(new Set())
  // Tracks awaiting "remove from playlist" confirmation. The ids outlive the
  // open flag so the dialog text doesn't flip to "0 tracks" while it fades out.
  const [pendingRemoval, setPendingRemoval] = useState<number[]>([])
  const [confirmingRemoval, setConfirmingRemoval] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const { data, isLoading, error } = useQuery<LikesPlaylist | PlaylistDetail>({
    queryKey: ["playlist", isLikes ? "likes" : playlistId],
    queryFn: (): Promise<LikesPlaylist | PlaylistDetail> =>
      isLikes ? fetchLikesPlaylist() : fetchPlaylist(playlistId!),
    enabled: isLikes || Number.isInteger(playlistId),
    staleTime: 30_000,
  })

  const {
    mutate: removeTracks,
    isPending: removing,
    error: removeError,
    reset: resetRemove,
  } = useMutation({
    mutationFn: (trackIds: number[]) => removePlaylistTracks(playlistId!, trackIds),
    onSuccess: (_result, trackIds) => {
      setSelectedIds((prev) => new Set([...prev].filter((trackId) => !trackIds.includes(trackId))))
      setConfirmingRemoval(false)
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

  function leaveDeletedPlaylist() {
    // Drop the cached detail: otherwise "Back" lands on a playlist that no
    // longer exists, rendered from cache, whose buttons all fail with 404.
    queryClient.removeQueries({ queryKey: ["playlist", playlistId] })
    queryClient.invalidateQueries({ queryKey: ["playlists"] })
    navigate("/", { replace: true })
  }

  const {
    mutate: handleDelete,
    isPending: deleting,
    error: deleteError,
    reset: resetDelete,
  } = useMutation({
    mutationFn: () => deletePlaylist(playlistId!),
    onSuccess: leaveDeletedPlaylist,
    onError: (err) => {
      // Already gone (deleted in another tab, or a repeated click) — the
      // user's intent is fulfilled, so leave instead of silently staying.
      if (err instanceof ApiError && err.status === 404) leaveDeletedPlaylist()
    },
  })

  if (isLoading) return <PlaylistSkeleton />
  if (error || !data) {
    if (error && isNetworkError(error)) {
      return (
        <div className="p-8">
          <p className="text-muted-foreground text-sm">{t("status.reconnecting", { ns: "common" })}</p>
        </div>
      )
    }
    return (
      <div className="p-8">
        <p className="text-destructive text-sm">{t("notFound")}</p>
      </div>
    )
  }

  const detail = isLikes ? null : (data as PlaylistDetail)
  const editable = !isLikes && detail?.editable !== false
  const tracks = data.tracks as TrackSummary[]
  // Selection can outlive its tracks (moved/removed elsewhere, refetch) — only
  // count ids still in the playlist.
  const trackIdSet = new Set(tracks.map((track) => track.id))
  const selected = [...selectedIds].filter((trackId) => trackIdSet.has(trackId))

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
  //
  // Shuffle asks the server for a shuffled session rather than reordering one
  // afterwards, so the queue arrives already mixed and its first track — the
  // one that starts playing — is random.
  function playPlaylistFrom(trackId?: number, options?: { shuffle?: boolean }): Promise<void> {
    const play = isLikes ? playLikes(options) : playPlaylist(playlistId!, options)
    return play.then((envelope) => playFromEnvelope(envelope, trackId)).catch(() => {})
  }

  function handleEdit() {
    if (detail) openCreatePlaylist({ playlist: detail })
  }

  function askRemove(trackIds: number[]) {
    resetRemove()
    setPendingRemoval(trackIds)
    setConfirmingRemoval(true)
  }

  function askDelete() {
    resetDelete()
    setConfirmingDelete(true)
  }

  const pendingTrack = pendingRemoval.length === 1
    ? tracks.find((track) => track.id === pendingRemoval[0])
    : undefined

  return (
    <div className="space-y-8 pb-8">
      {/* Header */}
      <CollectionHeader
        above={
          <button
            onClick={() => navigate(-1)}
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ChevronLeft size={16} />
            {t("actions.back", { ns: "common" })}
          </button>
        }
        artwork={
          isLikes ? (
            <LikesArtwork />
          ) : (
            <ArtworkImage
              src={detail?.artwork?.url}
              alt={data.title}
              size={176}
              className="rounded-lg shrink-0"
              fallbackLetter="P"
              expandable
            />
          )
        }
        kicker={t("playlistKicker")}
        title={data.title}
        truncateTitle={false}
        meta={
          <>
            {detail?.description && <p>{detail.description}</p>}
            <p className={detail?.description ? "mt-1" : undefined}>{t("trackCount", { count: tracks.length })}</p>
          </>
        }
        actions={
          <>
            <Button size="sm" onClick={() => playPlaylistFrom()} className="gap-2">
              <Play size={14} fill="currentColor" strokeWidth={0} />
              {t("play")}
            </Button>
            {tracks.length > 0 && (
              <>
                <Button
                  size="icon-sm"
                  variant="outline"
                  aria-label={t("shuffle")}
                  title={t("shuffle")}
                  onClick={() => playPlaylistFrom(undefined, { shuffle: true })}
                >
                  <Shuffle size={14} />
                </Button>
                <DownloadMenu
                  href={isLikes
                    ? "/api/v1/playlists/likes/download"
                    : `/api/v1/playlists/${playlistId}/download`}
                />
              </>
            )}
            {editable && (
              <>
                <Button
                  size="icon-sm"
                  variant="outline"
                  onClick={handleEdit}
                  aria-label={t("edit")}
                  title={t("edit")}
                >
                  <Pencil size={14} />
                </Button>
                <Button
                  size="icon-sm"
                  variant="outline"
                  onClick={askDelete}
                  disabled={deleting}
                  aria-label={t("delete")}
                  title={t("delete")}
                  className="text-destructive hover:text-destructive"
                >
                  <Trash2 size={14} />
                </Button>
              </>
            )}
          </>
        }
      />

      {/* Selection bar */}
      {editable && selected.length > 0 && (
        <div className="px-4 sm:px-6">
          <div className="flex flex-wrap items-center gap-2 rounded-md bg-muted/60 px-4 py-2 text-sm">
            <span className="mr-1">{t("selectedCount", { count: selected.length })}</span>
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5"
              onClick={() => openAddToPlaylist(selected, data.title)}
            >
              <ListPlus size={14} />
              {t("selection.addTo")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5"
              onClick={() => openAddToPlaylist(selected, data.title, { moveFrom: playlistId! })}
            >
              <FolderInput size={14} />
              {t("selection.moveTo")}
            </Button>
            <Button
              size="sm"
              variant="destructive"
              className="gap-1.5"
              onClick={() => askRemove(selected)}
              disabled={removing}
            >
              <Trash2 size={14} />
              {t("selection.remove")}
            </Button>
            <button
              onClick={() => setSelectedIds(new Set())}
              className="ml-auto flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors"
            >
              <X size={14} />
              {t("actions.cancel", { ns: "common" })}
            </button>
          </div>
        </div>
      )}

      {/* Tracks */}
      <div className="px-4 sm:px-6">
        <VirtualTrackList
          tracks={tracks}
          sourceLabel={data.title}
          selectable={editable}
          selectedIds={new Set(selected)}
          onToggleSelect={toggleSelect}
          onReorder={editable ? (trackIds) => reorder(trackIds) : undefined}
          onPlayTrack={(trackId) => playPlaylistFrom(trackId)}
          onRemoveTrack={editable ? (trackId) => askRemove([trackId]) : undefined}
        />
      </div>

      <ConfirmDialog
        open={confirmingRemoval}
        title={t("removeDialog.title", { count: pendingRemoval.length })}
        description={pendingTrack
          ? t("removeDialog.descriptionOne", { track: pendingTrack.title, playlist: data.title })
          : t("removeDialog.descriptionMany", { count: pendingRemoval.length, playlist: data.title })}
        confirmLabel={t("removeDialog.confirm")}
        pending={removing}
        error={removeError ? t("actionFailed") : null}
        onConfirm={() => removeTracks(pendingRemoval)}
        onCancel={() => setConfirmingRemoval(false)}
      />

      <ConfirmDialog
        open={confirmingDelete}
        title={t("deleteDialog.title")}
        description={t("deleteDialog.description", { title: data.title, count: tracks.length })}
        confirmLabel={t("deleteDialog.confirm")}
        pending={deleting}
        error={deleteError && !(deleteError instanceof ApiError && deleteError.status === 404)
          ? t("actionFailed")
          : null}
        onConfirm={() => handleDelete()}
        onCancel={() => setConfirmingDelete(false)}
      />
    </div>
  )
}
