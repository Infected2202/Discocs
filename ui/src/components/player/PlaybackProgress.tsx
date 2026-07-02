import { usePlayerStore } from "@/store/playerStore"

// Leaf components isolating the high-frequency `currentTime` subscription.
//
// The audio element fires `timeupdate` ~4×/sec. If a large component (PlayerBar,
// ExpandedPlayer) reads `currentTime` at its top level, the whole subtree — queue,
// menus, controls — re-renders on every tick. These leaves subscribe on their own
// so only a single `<span>` / progress fill updates per tick.

export function formatTime(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00"
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, "0")}`
}

interface SeekIndicatorsProps {
  fillClassName: string
  thumbClassName: string
  fillStyle?: React.CSSProperties
  /** When set (active drag), overrides the live progress. `null` → use currentTime. */
  override?: number | null
}

/**
 * Renders the seek bar fill (width) and thumb (left). Self-subscribes to
 * currentTime/duration; while dragging, the parent passes `override` so the
 * indicators follow the pointer instead of playback.
 */
export function SeekIndicators({
  fillClassName,
  thumbClassName,
  fillStyle,
  override = null,
}: SeekIndicatorsProps) {
  const currentTime = usePlayerStore((s) => s.currentTime)
  const duration = usePlayerStore((s) => s.duration)
  const progress = override ?? (duration > 0 ? currentTime / duration : 0)
  const pct = progress * 100

  return (
    <>
      <div className={fillClassName} style={{ ...fillStyle, width: `${pct}%` }} />
      <div className={thumbClassName} style={{ left: `calc(${pct}% - 6px)` }} />
    </>
  )
}

interface TimeReadoutProps {
  variant: "inline" | "split"
  className?: string
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
