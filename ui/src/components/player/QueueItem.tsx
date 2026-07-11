import { memo } from "react"
import { ThumbsUp } from "lucide-react"
import { cn } from "@/lib/utils"
import { usePlayerStore } from "@/store/playerStore"
import { useNavidromeStore } from "@/store/navidromeStore"
import ArtworkImage from "@/components/media/ArtworkImage"
import TrackMenu from "@/components/media/TrackMenu"
import { patchQueue } from "@/api/playback"
import { QueueItemLiveTime, formatTime } from "@/components/player/PlaybackProgress"
import type { TrackSummary } from "@/api/types"

export interface QueueItemProps {
  readonly track: TrackSummary | null
  readonly trackId?: number
  /** Queue/pool item id — used to jump. Omit for rows that are not jumpable. */
  readonly itemId?: string
  readonly variant?: "queue" | "autoplay"
  readonly isCurrent?: boolean
  readonly dimmed?: boolean
}

function QueueItem({ track, trackId, itemId, variant = "queue", isCurrent, dimmed }: QueueItemProps) {
  const id = track?.id ?? trackId
  const sessionId      = usePlayerStore((s) => s.session?.id)
  const refreshQueue   = usePlayerStore((s) => s.refreshQueue)
  const jumpToQueueItem    = usePlayerStore((s) => s.jumpToQueueItem)
  const jumpToAutoplayItem = usePlayerStore((s) => s.jumpToAutoplayItem)
  const toggleLike     = useNavidromeStore((s) => s.toggleLike)
  const liked          = useNavidromeStore((s) => track ? s.likedIds.has(track.id) : false)

  // Stable-per-render handler; store actions are stable references so memo holds.
  const jumpToItem = variant === "autoplay" ? jumpToAutoplayItem : jumpToQueueItem
  const onClick = isCurrent || !itemId ? undefined : () => jumpToItem(itemId)

  let durationLabel = ""
  if (track?.duration) {
    durationLabel = formatTime(track.duration)
  }

  // Only real (non-current) queue rows can be removed — autoplay-pool rows
  // aren't in the queue yet, and the currently-playing item can't be pulled
  // out from under itself.
  const canRemove = variant === "queue" && !!itemId && !isCurrent
  async function handleRemove() {
    if (!sessionId || !itemId) return
    await patchQueue(sessionId, { operation: "remove", queue_item_id: itemId })
    await refreshQueue()
  }

  return (
    <div
      className={cn(
        "group/row flex items-center gap-3 px-4 py-2 border-b border-border/30 last:border-0 transition-colors",
        isCurrent ? "bg-muted/50" : "hover:bg-muted/40",
        dimmed && "opacity-60",
      )}
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
            liked ? "text-primary" : "text-muted-foreground opacity-0 group-hover/row:opacity-100 hover:text-foreground",
          )}
        >
          <ThumbsUp size={13} />
        </button>
      )}

      <div className="flex items-center gap-1.5 shrink-0">
        {isCurrent && <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />}
        <span className="text-xs text-muted-foreground tabular-nums">
          {isCurrent ? <QueueItemLiveTime /> : durationLabel}
        </span>
      </div>

      {track && (
        <TrackMenu track={track} onRemoveFromQueue={canRemove ? handleRemove : undefined} />
      )}
    </div>
  )
}

// Memoized: parent players re-map the queue whenever the store changes, but a
// row only needs to re-render when its own props change. Without this, every
// currentTime tick that reaches the parent would re-render the whole list.
export default memo(QueueItem)
