import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import ArtworkImage from "@/components/media/ArtworkImage"
import { useUIStore } from "@/store/uiStore"
import { addTracksToPlaylist, fetchPlaylists } from "@/api/playlists"
import type { PlaylistSummary } from "@/api/types"

const RECENT_COUNT = 4

export default function AddToPlaylistDialog() {
  const trackIds = useUIStore((s) => s.addToPlaylistTrackIds)
  const defaultTitle = useUIStore((s) => s.addToPlaylistDefaultTitle)
  const close = useUIStore((s) => s.closeAddToPlaylist)
  const openCreatePlaylist = useUIStore((s) => s.openCreatePlaylist)
  const queryClient = useQueryClient()
  const open = trackIds !== null

  const { data, isLoading } = useQuery({
    queryKey: ["playlists"],
    queryFn: () => fetchPlaylists({ limit: 200 }),
    enabled: open,
  })

  const { mutate: addTo, isPending } = useMutation({
    mutationFn: (playlistId: number) => addTracksToPlaylist(playlistId, trackIds ?? []),
    onSuccess: (_result, playlistId) => {
      queryClient.invalidateQueries({ queryKey: ["playlists"] })
      queryClient.invalidateQueries({ queryKey: ["playlist", playlistId] })
      close()
    },
  })

  // list_playlists is already ordered by updated_at DESC
  const playlists = (data?.items ?? []).filter((playlist) => playlist.editable !== false)
  const recent = playlists.slice(0, RECENT_COUNT)

  function handleNewPlaylist() {
    openCreatePlaylist({ trackIds: trackIds ?? [], defaultTitle: defaultTitle ?? undefined })
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) close() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add to playlist</DialogTitle>
        </DialogHeader>

        {isLoading && <p className="text-sm text-muted-foreground">Loading playlists…</p>}

        {!isLoading && recent.length > 0 && (
          <div className="flex flex-col gap-2">
            <p className="text-xs text-muted-foreground uppercase tracking-wide">Recent</p>
            <div className="grid grid-cols-4 gap-3">
              {recent.map((playlist) => (
                <button
                  key={playlist.id}
                  type="button"
                  disabled={isPending}
                  onClick={() => addTo(playlist.id)}
                  className="group flex flex-col gap-1.5 text-left disabled:opacity-50"
                >
                  <ArtworkImage
                    src={playlist.artwork.url}
                    alt={playlist.title}
                    className="aspect-square w-full rounded-md object-cover group-hover:opacity-80"
                    fallbackLetter={playlist.title[0]}
                  />
                  <span className="text-xs truncate">{playlist.title}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {!isLoading && (
          <div className="flex flex-col gap-2">
            <p className="text-xs text-muted-foreground uppercase tracking-wide">All playlists</p>
            {playlists.length === 0 && (
              <p className="text-sm text-muted-foreground">No playlists yet.</p>
            )}
            {playlists.length > 0 && (
              <div className="max-h-64 overflow-y-auto flex flex-col gap-0.5 -mx-2">
                {playlists.map((playlist: PlaylistSummary) => (
                  <button
                    key={playlist.id}
                    type="button"
                    disabled={isPending}
                    onClick={() => addTo(playlist.id)}
                    className="flex items-center gap-3 rounded-md px-2 py-1.5 text-left hover:bg-muted disabled:opacity-50"
                  >
                    <ArtworkImage
                      src={playlist.artwork.url}
                      alt={playlist.title}
                      size={36}
                      className="rounded"
                      fallbackLetter={playlist.title[0]}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm truncate">{playlist.title}</span>
                      <span className="block text-xs text-muted-foreground">
                        {playlist.track_count} tracks
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <Button variant="outline" className="gap-2" onClick={handleNewPlaylist}>
          <Plus size={15} />
          New playlist
        </Button>
      </DialogContent>
    </Dialog>
  )
}
