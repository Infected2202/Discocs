import { useState } from "react"
import { useNavigate } from "react-router"
import { useTranslation } from "react-i18next"
import { MoreHorizontal, Play, ListEnd, ListPlus, ListMinus, ListX, User, Disc3, Radio, Download, Share2 } from "lucide-react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Button } from "@/components/ui/button"
import { apiFetch } from "@/api/client"
import { usePlayerStore } from "@/store/playerStore"
import { cn } from "@/lib/utils"
import { useUIStore } from "@/store/uiStore"
import type { PlaybackEnvelope, TrackSummary, ReleaseTrackItem } from "@/api/types"
import CreateShareDialog from "@/components/share/CreateShareDialog"
import { useShareCapabilities } from "@/api/shares"

interface TrackMenuProps {
  readonly track: TrackSummary | ReleaseTrackItem
  readonly sourceLabel?: string
  /** When set, "Play" plays the whole collection starting at this track instead of just this track. */
  readonly onPlayTrack?: (trackId: number) => void
  /** When set, adds a "Remove from queue" item — for rows that live in the current playback queue. */
  readonly onRemoveFromQueue?: () => void
  /** When set, adds a "Remove from playlist" item — for rows of an editable playlist. */
  readonly onRemoveFromPlaylist?: () => void
  /** Lets player surfaces keep their own control sizing while sharing the same menu. */
  readonly triggerClassName?: string
  readonly triggerIconSize?: number
}

export default function TrackMenu({
  track,
  sourceLabel,
  onPlayTrack,
  onRemoveFromQueue,
  onRemoveFromPlaylist,
  triggerClassName,
  triggerIconSize = 15,
}: TrackMenuProps) {
  const { t } = useTranslation("media")
  const navigate = useNavigate()
  const playSource     = usePlayerStore((s) => s.playSource)
  const adoptInstantMix = usePlayerStore((s) => s.adoptInstantMix)
  const playNext       = usePlayerStore((s) => s.playNext)
  const openAddToPlaylist = useUIStore((s) => s.openAddToPlaylist)
  const release = track.release
  const [shareOpen, setShareOpen] = useState(false)
  const { data: shareCapabilities } = useShareCapabilities()
  // "Original" is whatever is stored: name it, and call FLAC what it is.
  const audioFormat = track.audio_format?.toUpperCase()
  const originalLabel = (() => {
    if (!audioFormat) return t("trackMenu.downloadOriginal")
    if (audioFormat === "FLAC") return "FLAC"
    return t("trackMenu.downloadOriginalAs", { format: audioFormat })
  })()

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
      await adoptInstantMix(envelope)
    } catch { /* ignore */ }
  }

  async function handlePlayNext() {
    await playNext(track.id, sourceLabel ?? track.title)
  }

  return (
    <>
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

        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <Download size={14} className="mr-2" />
            {t("trackMenu.download")}
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownMenuItem asChild>
              <a href={`/api/v1/tracks/${track.id}/download?format=mp3_192`} download>MP3 192</a>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <a href={`/api/v1/tracks/${track.id}/download?format=mp3_320`} download>MP3 320</a>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <a href={`/api/v1/tracks/${track.id}/download`} download>{originalLabel}</a>
            </DropdownMenuItem>
          </DropdownMenuSubContent>
        </DropdownMenuSub>

        {shareCapabilities?.can_create && (
          <DropdownMenuItem onSelect={() => setShareOpen(true)}>
            <Share2 size={14} className="mr-2" />
            {t("trackMenu.share")}
          </DropdownMenuItem>
        )}

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

        {onRemoveFromPlaylist && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={onRemoveFromPlaylist}>
              <ListMinus size={14} className="mr-2" />
              {t("trackMenu.removeFromPlaylist")}
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
    <CreateShareDialog
      open={shareOpen}
      onOpenChange={setShareOpen}
      sourceType="track"
      sourceId={track.id}
      sourceTitle={track.title}
    />
    </>
  )
}
