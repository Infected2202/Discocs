import { useState } from "react"
import { useNavigate } from "react-router"
import { ThumbsUp, MoreHorizontal, ListEnd, Radio, User, Disc3 } from "lucide-react"
import { cn } from "@/lib/utils"
import { usePlayerStore } from "@/store/playerStore"
import { useNavidromeStore } from "@/store/navidromeStore"
import ArtworkImage from "@/components/media/ArtworkImage"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { apiFetch } from "@/api/client"
import { patchQueue } from "@/api/playback"
import type { TrackSummary, PlaybackEnvelope } from "@/api/types"

function formatTime(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00"
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, "0")}`
}

export interface QueueItemProps {
  track: TrackSummary | null
  trackId?: number
  isCurrent?: boolean
  dimmed?: boolean
  time?: string
  onClick?: () => void
}

export default function QueueItem({ track, trackId, isCurrent, dimmed, time, onClick }: QueueItemProps) {
  const [hovered, setHovered] = useState(false)
  const navigate = useNavigate()
  const id = track?.id ?? trackId
  const sessionId      = usePlayerStore((s) => s.session?.id)
  const playSource     = usePlayerStore((s) => s.playSource)
  const refreshQueue   = usePlayerStore((s) => s.refreshQueue)
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  const isLiked        = useNavidromeStore((s) => s.isLiked)
  const toggleLike     = useNavidromeStore((s) => s.toggleLike)
  const liked = track ? isLiked(track.id) : false

  async function handleInstantMix() {
    if (!track) return
    try {
      const envelope = await apiFetch<PlaybackEnvelope>(`/tracks/${track.id}/instant-mix`, { method: "POST" })
      await playFromEnvelope(envelope)
    } catch { /* ignore */ }
  }

  async function handlePlayNext() {
    if (!track) return
    if (!sessionId) { await playSource("track", track.id, track.title); return }
    await patchQueue(sessionId, { operation: "add", track_id: track.id })
    await refreshQueue()
  }

  return (
    <div
      className={cn(
        "group/qrow flex items-center gap-3 px-4 py-2 border-b border-border/30 last:border-0 transition-colors",
        isCurrent ? "bg-muted/50" : "hover:bg-muted/40",
        dimmed && "opacity-60",
      )}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        onClick={onClick}
        disabled={!onClick && !isCurrent}
        className={cn("flex items-center gap-3 flex-1 min-w-0 text-left", (!onClick && !isCurrent) && "cursor-default")}
      >
        <div className="w-9 h-9 rounded shrink-0 overflow-hidden">
          <ArtworkImage
            src={track?.artwork?.url}
            alt={track?.title ?? ""}
            size={36}
            className="w-9 h-9"
            fallbackLetter={track?.title?.[0]}
          />
        </div>
        <div className="flex-1 min-w-0">
          <p className={cn("text-sm font-medium truncate", isCurrent && "text-primary")}>
            {track?.title ?? `Track #${id}`}
          </p>
          <p className="text-xs text-muted-foreground truncate">
            {track?.artists?.map((a) => a.name).join(", ") ?? "—"}
          </p>
        </div>
      </button>

      {track && (
        <button
          onClick={() => toggleLike(track.id)}
          className={cn(
            "shrink-0 p-1 rounded transition-colors",
            liked ? "text-primary" : "text-muted-foreground opacity-0 group-hover/qrow:opacity-100 hover:text-foreground",
          )}
        >
          <ThumbsUp size={13} />
        </button>
      )}

      <div className="flex items-center gap-1.5 shrink-0">
        {isCurrent && <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />}
        <span className="text-xs text-muted-foreground tabular-nums">
          {time ?? (track?.duration ? formatTime(track.duration) : "")}
        </span>
      </div>

      {track && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="shrink-0 p-1 rounded text-muted-foreground opacity-0 group-hover/qrow:opacity-100 hover:text-foreground transition-colors">
              <MoreHorizontal size={15} />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem onClick={handlePlayNext}>
              <ListEnd size={13} className="mr-2" /> Play next
            </DropdownMenuItem>
            <DropdownMenuItem onClick={handleInstantMix}>
              <Radio size={13} className="mr-2" /> Instant mix
            </DropdownMenuItem>
            {track.artists.length > 0 && (
              <>
                <DropdownMenuSeparator />
                {track.artists.map((a) => (
                  <DropdownMenuItem key={a.id} onClick={() => navigate(`/artists/${a.id}`)}>
                    <User size={13} className="mr-2" /> Go to {a.name}
                  </DropdownMenuItem>
                ))}
              </>
            )}
            {track.release && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => navigate(`/releases/${track.release!.id}`)}>
                  <Disc3 size={13} className="mr-2" /> Go to {track.release.title}
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}
