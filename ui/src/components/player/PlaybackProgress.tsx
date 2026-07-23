import { usePlayerStore } from "@/store/playerStore"
import { cn } from "@/lib/utils"

// Leaf components isolating the high-frequency `currentTime` subscription.
//
// The audio element fires `timeupdate` ~4×/sec. If a large component (PlayerBar,
// ExpandedPlayer) reads `currentTime` at its top level, the whole subtree — queue,
// menus, controls — re-renders on every tick. These leaves subscribe on their own
// so only a single `<span>` / progress fill updates per tick.

export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00"
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, "0")}`
}

interface SeekIndicatorsProps {
  readonly fillClassName: string
  readonly thumbClassName: string
  readonly fillStyle?: React.CSSProperties
  /** When set (active drag), overrides the live progress. `null` → use currentTime. */
  readonly override?: number | null
  /** Renders a lighter fill behind the playback fill showing download progress. */
  readonly bufferedClassName?: string
  /**
   * Pulsing dot pinned to the bar's right edge, shown whenever the next queue
   * track is being prefetched; stops pulsing once it's fully buffered.
   */
  readonly nextBufferDotClassName?: string
  /**
   * Pulsing dot at the current seek target, shown while a seek on a
   * not-yet-cached track is paused waiting for the Blob swap to land.
   */
  readonly seekBufferDotClassName?: string
}

/**
 * Renders the seek bar buffered fill, playback fill (width) and thumb (left).
 * Self-subscribes to currentTime/duration/bufferedRanges; while dragging, the
 * parent passes `override` so the indicators follow the pointer instead of
 * playback.
 */
export function SeekIndicators({
  fillClassName,
  thumbClassName,
  fillStyle,
  override = null,
  bufferedClassName,
  nextBufferDotClassName,
  seekBufferDotClassName,
}: SeekIndicatorsProps) {
  const currentTime = usePlayerStore((s) => s.currentTime)
  const duration = usePlayerStore((s) => s.duration)
  const bufferedRanges = usePlayerStore((s) => s.bufferedRanges)
  const nextTrackBuffer = usePlayerStore((s) => s.nextTrackBuffer)
  const seekBuffering = usePlayerStore((s) => s.seekBuffering)
  const progress = override ?? (duration > 0 ? currentTime / duration : 0)
  const pct = progress * 100

  return (
    <>
      {bufferedClassName && bufferedRanges.map((range, index) => (
        <div
          className={bufferedClassName}
          data-buffered-range
          key={`${range.start}-${range.end}-${index}`}
          style={{
            left: `${range.start * 100}%`,
            width: `${Math.max(0, range.end - range.start) * 100}%`,
          }}
        />
      ))}
      <div className={fillClassName} style={{ ...fillStyle, width: `${pct}%` }} />
      <div className={thumbClassName} style={{ left: `calc(${pct}% - 6px)` }} />
      {nextBufferDotClassName && nextTrackBuffer && (
        <div
          className={cn(nextBufferDotClassName, !nextTrackBuffer.ready && "animate-pulse")}
          data-next-buffer-dot
          data-ready={nextTrackBuffer.ready}
        />
      )}
      {seekBufferDotClassName && seekBuffering && (
        <div
          className={cn(seekBufferDotClassName, "animate-pulse")}
          data-seek-buffer-dot
          style={{ left: `calc(${pct}% - 3px)` }}
        />
      )}
    </>
  )
}

interface TimeReadoutProps {
  readonly variant: "inline" | "split"
  readonly className?: string
}

/**
 * Renders the `currentTime / duration` label. Self-subscribes so the parent
 * does not re-render on every tick.
 */
export function TimeReadout({ variant, className }: TimeReadoutProps) {
  const currentTime = usePlayerStore((s) => s.currentTime)
  const duration = usePlayerStore((s) => s.duration)

  if (variant === "split") {
    return (
      <>
        <span>{formatTime(currentTime)}</span>
        <span>{formatTime(duration)}</span>
      </>
    )
  }

  return (
    <span className={className}>
      {formatTime(currentTime)} / {formatTime(duration)}
    </span>
  )
}

/** Live time cell for the current queue item: `0:34 / 3:20`. */
export function QueueItemLiveTime() {
  const currentTime = usePlayerStore((s) => s.currentTime)
  const duration = usePlayerStore((s) => s.duration)
  return <>{`${formatTime(currentTime)} / ${formatTime(duration)}`}</>
}
