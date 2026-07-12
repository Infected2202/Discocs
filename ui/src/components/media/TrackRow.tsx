import { useState } from "react"
import { Link } from "react-router"
import { Play, Pause } from "lucide-react"
import { cn } from "@/lib/utils"
import ArtworkImage from "./ArtworkImage"
import LikeButton from "./LikeButton"
import TrackMenu from "./TrackMenu"
import { usePlayerStore } from "@/store/playerStore"
import type { TrackSummary, ReleaseTrackItem, ArtistTopTrack } from "@/api/types"

type TrackRowTrack = TrackSummary | ReleaseTrackItem | ArtistTopTrack

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || !Number.isFinite(seconds)) return ""
  const total = Math.round(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, "0")}`
}

const playCountFormatter = new Intl.NumberFormat("ru", { notation: "compact", compactDisplay: "short" })

function formatPlayCount(count: number): string {
  return playCountFormatter.format(count)
}

function renderIndex(index: number | undefined, isActive: boolean): React.ReactNode {
  const indexLabel = index == null ? "" : index + 1

  return (
    <span className={cn("text-xs tabular-nums", isActive ? "text-primary" : "text-muted-foreground")}>
      {indexLabel}
    </span>
  )
}

function renderPlayIcon(isPlaying: boolean): React.ReactNode {
  if (isPlaying) {
    return <Pause size={14} fill="currentColor" strokeWidth={0} />
  }

  return <Play size={14} fill="currentColor" strokeWidth={0} />
}

function renderTrackTitle(
  track: TrackRowTrack,
  isActive: boolean,
): React.ReactNode {
  if (!track.release) {
    return <p className={cn("truncate text-sm font-medium", isActive && "text-primary")}>{track.title}</p>
  }

  return (
    <Link
      to={`/releases/${track.release.id}`}
      className={cn("truncate text-sm font-medium hover:underline", isActive ? "text-primary" : "")}
      onClick={(event) => event.stopPropagation()}
    >
      {track.title}
    </Link>
  )
}

function renderMetric(track: TrackRowTrack): React.ReactNode {
  if ("play_count" in track) {
    if (track.play_count <= 0) {
      return null
    }

    return (
      <>
        {formatPlayCount(track.play_count)}
        <span className="hidden sm:inline"> прослушиваний</span>
      </>
    )
  }

  return formatDuration(track.duration)
}

interface TrackRowProps {
  readonly track: TrackRowTrack
  readonly index?: number
  readonly showArtwork?: boolean
  readonly showRelease?: boolean
  readonly sourceLabel?: string
  /** When set, playing this row queues the whole release (starting at this track) instead of just this track. */
  readonly releaseId?: number
}

export default function TrackRow({
  track,
  index,
  showArtwork = true,
  showRelease = true,
  sourceLabel,
  releaseId,
}: TrackRowProps) {
  const [hovered, setHovered] = useState(false)

  // Individual selectors — avoids infinite loop from object selector in React 19
  const currentTrackId = usePlayerStore((s) => s.currentTrackId)
  const playbackState = usePlayerStore((s) => s.playbackState)
  const playSource = usePlayerStore((s) => s.playSource)
  const togglePlay = usePlayerStore((s) => s.togglePlay)

  const isActive = currentTrackId === track.id
  const isPlaying = isActive && playbackState === "playing"
  const shouldShowPlayControl = hovered || isActive

  function handlePlay(event: React.MouseEvent) {
    event.stopPropagation()
    if (isActive) {
      togglePlay()
      return
    }

    if (releaseId) {
      playSource("release", releaseId, sourceLabel ?? track.release?.title ?? track.title, track.id)
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
      <td className="w-10 pl-3 pr-1 py-2 text-center">
        <button
          onClick={handlePlay}
          className="mx-auto flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:text-foreground"
          aria-label={isPlaying ? "Pause" : "Play"}
        >
          {shouldShowPlayControl ? renderPlayIcon(isPlaying) : renderIndex(index, isActive)}
        </button>
      </td>

      {showArtwork && (
        <td className="w-10 py-1.5 pr-3">
          <button onClick={handlePlay} className="block" aria-label={isPlaying ? "Pause" : "Play"}>
            <ArtworkImage
              src={track.artwork?.url}
              alt={track.title}
              size={36}
              fallbackLetter={track.title[0]}
            />
          </button>
        </td>
      )}

      <td className="min-w-0 py-2 pr-4">
        <div className="min-w-0">
          {renderTrackTitle(track, isActive)}
          {track.artists.length > 0 && (
            <p className="truncate text-xs text-muted-foreground">
              {track.artists.map((artist, artistIndex) => (
                <span key={artist.id}>
                  {artistIndex > 0 && ", "}
                  <Link
                    to={`/artists/${artist.id}`}
                    className="transition-colors hover:text-foreground hover:underline"
                    onClick={(event) => event.stopPropagation()}
                  >
                    {artist.name}
                  </Link>
                </span>
              ))}
            </p>
          )}
        </div>
      </td>

      {showRelease && (
        <td className="hidden min-w-0 max-w-[180px] py-2 pr-4 md:table-cell">
          {track.release && (
            <Link
              to={`/releases/${track.release.id}`}
              className="block truncate text-xs text-muted-foreground hover:text-foreground hover:underline"
              onClick={(event) => event.stopPropagation()}
            >
              {track.release.title}
            </Link>
          )}
        </td>
      )}

      <td className="whitespace-nowrap py-2 pr-2 text-right text-xs tabular-nums text-muted-foreground">
        {renderMetric(track)}
      </td>

      <td className="w-8 py-2">
        <LikeButton entity="track" id={track.id} variant="row" size={13} />
      </td>

      <td className="w-8 py-2 pr-2">
        <TrackMenu track={track} sourceLabel={sourceLabel} releaseId={releaseId} />
      </td>
    </tr>
  )
}
