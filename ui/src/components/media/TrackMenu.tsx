import { useNavigate } from "react-router"
import { useTranslation } from "react-i18next"
import { MoreHorizontal, Play, ListEnd, ListPlus, ListX, User, Disc3, Radio, Download } from "lucide-react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Button } from "@/components/ui/button"
import { apiFetch } from "@/api/client"
import { patchQueue } from "@/api/playback"
import { usePlayerStore } from "@/store/playerStore"
import { cn } from "@/lib/utils"
import { useUIStore } from "@/store/uiStore"
import type { PlaybackEnvelope, TrackSummary, ReleaseTrackItem } from "@/api/types"

interface TrackMenuProps {
  readonly track: TrackSummary | ReleaseTrackItem
  readonly sourceLabel?: string
  /** When set, "Play" plays the whole collection starting at this track instead of just this track. */
  readonly onPlayTrack?: (trackId: number) => void
  /** When set, adds a "Remove from queue" item — for rows that live in the current playback queue. */
  readonly onRemoveFromQueue?: () => void
  /** Lets player surfaces keep their own control sizing while sharing the same menu. */
  readonly triggerClassName?: string
  readonly triggerIconSize?: number
}

export default function TrackMenu({
  track,
  sourceLabel,
  onPlayTrack,
  onRemoveFromQueue,
  triggerClassName,
  triggerIconSize = 15,
}: TrackMenuProps) {
  const { t } = useTranslation("media")
  const navigate = useNavigate()
  const sessionId      = usePlayerStore((s) => s.session?.id)
  const playSource     = usePlayerStore((s) => s.playSource)
  const refreshQueue   = usePlayerStore((s) => s.refreshQueue)
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  const openAddToPlaylist = useUIStore((s) => s.openAddToPlaylist)
  const release = track.release

  async function handlePlay() {
    if (onPlayTrack) {
      onPlayTrack(track.id)
    } else {
      await playSource("track", track.id, sourceLabel ?? track.title)
    }
  }

  async function handleInstantMix() {
    try {
      const envelope = await apiFetch<PlaybackEnvelope>(
        `/api/v1/tracks/${track.id}/instant-mix`,
        { method: "POST" }
      )
      await playFromEnvelope(envelope)
    } catch { /* ignore */ }
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
          className={cn(
            "track-menu-trigger h-7 w-7 text-muted-foreground data-[state=open]:opacity-100 focus-visible:opacity-100",
            triggerClassName,
          )}
          aria-label={t("trackMenu.trackOptions")}
        >
          <MoreHorizontal size={triggerIconSize} style={{ width: triggerIconSize, height: triggerIconSize }} />
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuItem onClick={handlePlay}>
          <Play size={14} className="mr-2" />
          {t("trackMenu.play")}
        </DropdownMenuItem>

        <DropdownMenuItem onClick={handlePlayNext}>
          <ListEnd size={14} className="mr-2" />
          {t("trackMenu.playNext")}
        </DropdownMenuItem>

        <DropdownMenuItem onClick={handleInstantMix}>
          <Radio size={14} className="mr-2" />
          {t("trackMenu.instantMix")}
        </DropdownMenuItem>

        <DropdownMenuItem onClick={() => openAddToPlaylist([track.id])}>
          <ListPlus size={14} className="mr-2" />
          {t("trackMenu.addToPlaylist")}
        </DropdownMenuItem>

        <DropdownMenuItem asChild>
          <a href={`/api/v1/tracks/${track.id}/download`} download>
            <Download size={14} className="mr-2" />
            {t("trackMenu.download")}
          </a>
        </DropdownMenuItem>

        {track.artists.length > 0 && (
          <>
            <DropdownMenuSeparator />
            {track.artists.map((artist) => (
              <DropdownMenuItem key={artist.id} onClick={() => navigate(`/artists/${artist.id}`)}>
                <User size={14} className="mr-2" />
                {t("trackMenu.goTo", { name: artist.name })}
              </DropdownMenuItem>
            ))}
          </>
        )}

        {release && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => navigate(`/releases/${release.id}`)}>
              <Disc3 size={14} className="mr-2" />
              {t("trackMenu.goTo", { name: release.title })}
            </DropdownMenuItem>
          </>
        )}

        {onRemoveFromQueue && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={onRemoveFromQueue}>
              <ListX size={14} className="mr-2" />
              {t("trackMenu.removeFromQueue")}
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
