import { useState } from "react"
import { Link } from "react-router"
import { Play, Pause, ThumbsUp } from "lucide-react"
import { cn } from "@/lib/utils"
import ArtworkImage from "./ArtworkImage"
import TrackMenu from "./TrackMenu"
import { usePlayerStore } from "@/store/playerStore"
import { useNavidromeStore } from "@/store/navidromeStore"
import type { TrackSummary, ReleaseTrackItem, ArtistTopTrack } from "@/api/types"

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || !Number.isFinite(seconds)) return ""
  const total = Math.round(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, "0")}`
}

const playCountFormatter = new Intl.NumberFormat("ru", { notation: "compact", compactDisplay: "short" })

function formatPlayCount(count: number): string {
  return `${playCountFormatter.format(count)} прослушиваний`
}

interface TrackRowProps {
  readonly track: TrackSummary | ReleaseTrackItem | ArtistTopTrack
  readonly index?: number
  readonly showArtwork?: boolean
  readonly showRelease?: boolean
  readonly sourceLabel?: string
  readonly releaseContextId?: number
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
          {track.release ? (
            <Link
              to={`/releases/${track.release.id}`}
              className={cn("truncate text-sm font-medium hover:underline", isActive ? "text-primary" : "")}
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

      {/* Duration / play count */}
      <td className="py-2 pr-2 text-right text-xs text-muted-foreground tabular-nums whitespace-nowrap">
        {"play_count" in track
          ? track.play_count > 0 ? formatPlayCount(track.play_count) : ""
          : formatDuration(track.duration)}
      </td>

      {/* Like */}
      <td className="py-2 w-8">
        <button
          onClick={(e) => { e.stopPropagation(); toggleLike(track.id) }}
          className={cn(
            "flex h-7 w-7 items-center justify-center rounded transition-colors mx-auto",
            liked
              ? "text-primary opacity-100"
              : "text-muted-foreground opacity-100 md:opacity-0 md:group-hover/row:opacity-100 hover:text-foreground",
          )}
        >
          <ThumbsUp size={13} />
        </button>
      </td>

      {/* Menu */}
      <td className="py-2 pr-2 w-8">
        <TrackMenu track={track} sourceLabel={sourceLabel} />
      </td>
    </tr>
  )
}
