import { useNavigate } from "react-router"
import { MoreHorizontal, Play, ListEnd, User, Disc3 } from "lucide-react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Button } from "@/components/ui/button"
import { patchQueue } from "@/api/playback"
import { usePlayerStore } from "@/store/playerStore"
import type { TrackSummary, ReleaseTrackItem } from "@/api/types"

interface TrackMenuProps {
  track: TrackSummary | ReleaseTrackItem
  sourceLabel?: string
}

export default function TrackMenu({ track, sourceLabel }: TrackMenuProps) {
  const navigate = useNavigate()
  const sessionId = usePlayerStore((s) => s.session?.id)
  const playSource = usePlayerStore((s) => s.playSource)
  const refreshQueue = usePlayerStore((s) => s.refreshQueue)

  async function handlePlay() {
    await playSource("track", track.id, sourceLabel ?? track.title)
  }

  async function handlePlayNext() {
    if (!sessionId) {
      await playSource("track", track.id, sourceLabel ?? track.title)
      return
    }
    await patchQueue(sessionId, { operation: "add", track_id: track.id })
    await refreshQueue()
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-muted-foreground opacity-0 group-hover/row:opacity-100 data-[state=open]:opacity-100"
          aria-label="Track options"
        >
          <MoreHorizontal size={15} />
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuItem onClick={handlePlay}>
          <Play size={14} className="mr-2" />
          Play
        </DropdownMenuItem>

        <DropdownMenuItem onClick={handlePlayNext}>
          <ListEnd size={14} className="mr-2" />
          Play next
        </DropdownMenuItem>

        {track.artists.length > 0 && (
          <>
            <DropdownMenuSeparator />
            {track.artists.map((artist) => (
              <DropdownMenuItem key={artist.id} onClick={() => navigate(`/artists/${artist.id}`)}>
                <User size={14} className="mr-2" />
                Go to {artist.name}
              </DropdownMenuItem>
            ))}
          </>
        )}

        {track.release && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => navigate(`/releases/${track.release!.id}`)}>
              <Disc3 size={14} className="mr-2" />
              Go to {track.release.title}
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
