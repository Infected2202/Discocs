import { useState } from "react"
import { Link } from "react-router"
import { Play, Pause, ThumbsUp, Check } from "lucide-react"
import { cn } from "@/lib/utils"
import ArtworkImage from "./ArtworkImage"
import TrackMenu from "./TrackMenu"
import { usePlayerStore } from "@/store/playerStore"
import { useNavidromeStore } from "@/store/navidromeStore"
import type { TrackSummary } from "@/api/types"

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || !Number.isFinite(seconds)) return ""
  const total = Math.round(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, "0")}`
}

interface VirtualTrackRowProps {
  readonly track: TrackSummary
  readonly index: number
  readonly showArtwork?: boolean
  readonly showRelease?: boolean
  readonly sourceLabel?: string
  readonly selectable?: boolean
  readonly selected?: boolean
  readonly selectionActive?: boolean
  readonly onToggleSelect?: (trackId: number) => void
}

export function trackGridTemplates({
  showArtwork,
  showRelease,
  selectable,
}: Pick<VirtualTrackRowProps, "showArtwork" | "showRelease" | "selectable">): {
  mobile: string
  desktop: string
} {
  const common = [
    "40px",
    showArtwork ? "44px" : null,
    "minmax(0,1fr)",
  ]
  const trailing = [
    "80px", // duration
    "32px", // like
    "32px", // menu
    selectable ? "32px" : null, // selection checkbox
  ]
  const mobile = [...common, ...trailing].filter(Boolean).join(" ")
  const desktop = showRelease
    ? [...common, "minmax(0,180px)", ...trailing].filter(Boolean).join(" ")
    : mobile
  return { mobile, desktop }
}

export default function VirtualTrackRow({
  track,
  index,
  showArtwork = true,
  showRelease = true,
  sourceLabel,
  selectable = false,
  selected = false,
  selectionActive = false,
  onToggleSelect,
}: VirtualTrackRowProps) {
  const [hovered, setHovered] = useState(false)

  const currentTrackId = usePlayerStore((s) => s.currentTrackId)
  const playbackState  = usePlayerStore((s) => s.playbackState)
  const playSource     = usePlayerStore((s) => s.playSource)
  const togglePlay     = usePlayerStore((s) => s.togglePlay)
  const toggleLike     = useNavidromeStore((s) => s.toggleLike)
  const liked          = useNavidromeStore((s) => s.likedIds.has(track.id))

  const isActive  = currentTrackId === track.id
  const isPlaying = isActive && playbackState === "playing"

  function handlePlay(e: React.MouseEvent) {
    e.stopPropagation()
    if (isActive) {
      togglePlay()
    } else {
      playSource("track", track.id, sourceLabel ?? track.title)
    }
  }

  // The release cell is hidden below md. Its desktop-width grid column must
  // disappear too — otherwise the title's 1fr column collapses on phones.
  const gridTemplates = trackGridTemplates({ showArtwork, showRelease, selectable })

  return (
    <div
      data-track-row
      className={cn(
        "grid items-center border-b border-border/40 last:border-0 transition-colors hover:bg-muted/40 h-[52px] [grid-template-columns:var(--track-grid-mobile)] md:[grid-template-columns:var(--track-grid-desktop)]",
        isActive && "text-primary",
      )}
      style={{
        "--track-grid-mobile": gridTemplates.mobile,
        "--track-grid-desktop": gridTemplates.desktop,
      } as React.CSSProperties}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Index / play button (always on the left) */}
      <div className="pl-3 pr-1 text-center">
        <button
          onClick={handlePlay}
          className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:text-foreground mx-auto"
          aria-label={isPlaying ? "Pause" : "Play"}
        >
          {hovered || isActive ? (
            isPlaying ? (
              <Pause size={14} fill="currentColor" strokeWidth={0} />
            ) : (
              <Play size={14} fill="currentColor" strokeWidth={0} />
            )
          ) : (
            <span className={cn("text-xs tabular-nums", isActive ? "text-primary" : "text-muted-foreground")}>
              {index + 1}
            </span>
          )}
        </button>
      </div>

      {/* Artwork */}
      {showArtwork && (
        <div className="py-1.5 pr-2">
          <button onClick={handlePlay} className="block" aria-label={isPlaying ? "Pause" : "Play"}>
            <ArtworkImage
              src={track.artwork?.url}
              alt={track.title}
              size={36}
              fallbackLetter={track.title[0]}
            />
          </button>
        </div>
      )}

      {/* Title + artists */}
      <div className="py-2 pr-4 min-w-0">
        <div className="min-w-0">
          {track.release ? (
            <Link
              to={`/releases/${track.release.id}`}
              className={cn("truncate text-sm font-medium hover:underline block", isActive ? "text-primary" : "")}
              onClick={(e) => e.stopPropagation()}
            >
              {track.title}
            </Link>
          ) : (
            <p className={cn("truncate text-sm font-medium", isActive && "text-primary")}>
              {track.title}
            </p>
          )}
          {track.artists.length > 0 && (
            <p className="truncate text-xs text-muted-foreground">
              {track.artists.map((a, i) => (
                <span key={a.id}>
                  {i > 0 && ", "}
                  <Link
                    to={`/artists/${a.id}`}
                    className="hover:text-foreground hover:underline transition-colors"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {a.name}
                  </Link>
                </span>
              ))}
            </p>
          )}
        </div>
      </div>

      {/* Release */}
      {showRelease && (
        <div className="py-2 pr-4 min-w-0 hidden md:block">
          {track.release && (
            <Link
              to={`/releases/${track.release.id}`}
              className="truncate block text-xs text-muted-foreground hover:text-foreground hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              {track.release.title}
            </Link>
          )}
        </div>
      )}

      {/* Duration */}
      <div className="py-2 pr-2 text-right text-xs text-muted-foreground tabular-nums whitespace-nowrap">
        {formatDuration(track.duration)}
      </div>

      {/* Like */}
      <div className="py-2">
        <button
          onClick={(e) => { e.stopPropagation(); toggleLike(track.id) }}
          className={cn(
            "flex h-7 w-7 items-center justify-center rounded transition-colors mx-auto",
            liked
              ? "text-primary opacity-100"
              : "text-muted-foreground opacity-100 md:opacity-0 md:group-hover:opacity-100 hover:text-foreground",
          )}
        >
          <ThumbsUp size={13} />
        </button>
      </div>

      {/* Menu */}
      <div className="py-2 pr-2">
        <TrackMenu track={track} sourceLabel={sourceLabel} />
      </div>

      {/* Selection checkbox (right side) */}
      {selectable && (
        <div className="py-2 pr-2">
          {hovered || selectionActive ? (
            <button
              onClick={(e) => { e.stopPropagation(); onToggleSelect?.(track.id) }}
              role="checkbox"
              aria-checked={selected}
              aria-label={`Select ${track.title}`}
              className="flex h-7 w-7 items-center justify-center mx-auto"
            >
              <span
                className={cn(
                  "flex h-4 w-4 items-center justify-center rounded-sm border transition-colors",
                  selected
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-muted-foreground/60 hover:border-foreground",
                )}
              >
                {selected && <Check size={12} strokeWidth={3} />}
              </span>
            </button>
          ) : null}
        </div>
      )}
    </div>
  )
}
