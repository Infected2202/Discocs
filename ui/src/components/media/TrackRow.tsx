import { useState } from "react"
import { Link } from "react-router"
import { Play, Pause } from "lucide-react"
import { cn } from "@/lib/utils"
import ArtworkImage from "./ArtworkImage"
import TrackMenu from "./TrackMenu"
import { usePlayerStore } from "@/store/playerStore"
import type { TrackSummary, ReleaseTrackItem } from "@/api/types"

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || !Number.isFinite(seconds)) return ""
  const total = Math.round(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, "0")}`
}

interface TrackRowProps {
  track: TrackSummary | ReleaseTrackItem
  index?: number
  showArtwork?: boolean
  showRelease?: boolean
  sourceLabel?: string
  releaseContextId?: number
}

export default function TrackRow({
  track,
  index,
  showArtwork = true,
  showRelease = true,
  sourceLabel,
}: TrackRowProps) {
  const [hovered, setHovered] = useState(false)

  // Individual selectors — avoids infinite loop from object selector in React 19
  const currentTrackId = usePlayerStore((s) => s.currentTrackId)
  const playbackState = usePlayerStore((s) => s.playbackState)
  const playSource = usePlayerStore((s) => s.playSource)
  const togglePlay = usePlayerStore((s) => s.togglePlay)

  const isActive = currentTrackId === track.id
  const isPlaying = isActive && playbackState === "playing"

  function handlePlay(e: React.MouseEvent) {
    e.stopPropagation()
    if (isActive) {
      togglePlay()
    } else {
      playSource("track", track.id, sourceLabel ?? track.title)
    }
  }

  const artists = track.artists.map((a) => a.name).join(", ")

  return (
    <tr
      className={cn(
        "group/row border-b border-border/40 last:border-0 transition-colors hover:bg-muted/40",
        isActive && "text-primary",
      )}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Index / play button */}
      <td className="w-10 pl-3 pr-1 py-2 text-center">
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
              {index != null ? index + 1 : ""}
            </span>
          )}
        </button>
      </td>

      {/* Artwork */}
      {showArtwork && (
        <td className="w-10 py-1.5 pr-3">
          <ArtworkImage
            src={track.artwork?.url}
            alt={track.title}
            size={36}
            fallbackLetter={track.title[0]}
          />
        </td>
      )}

      {/* Title + artists */}
      <td className="py-2 pr-4 min-w-0">
        <div className="min-w-0">
          <p className={cn("truncate text-sm font-medium", isActive && "text-primary")}>
            {track.title}
          </p>
          {artists && (
            <p className="truncate text-xs text-muted-foreground">{artists}</p>
          )}
        </div>
      </td>

      {/* Release */}
      {showRelease && (
        <td className="py-2 pr-4 hidden md:table-cell min-w-0 max-w-[180px]">
          {track.release && (
            <Link
              to={`/releases/${track.release.id}`}
              className="truncate block text-xs text-muted-foreground hover:text-foreground hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              {track.release.title}
            </Link>
          )}
        </td>
      )}

      {/* Duration */}
      <td className="py-2 pr-2 text-right text-xs text-muted-foreground tabular-nums w-14">
        {formatDuration(track.duration)}
      </td>

      {/* Menu */}
      <td className="py-2 pr-2 w-8">
        <TrackMenu track={track} sourceLabel={sourceLabel} />
      </td>
    </tr>
  )
}
