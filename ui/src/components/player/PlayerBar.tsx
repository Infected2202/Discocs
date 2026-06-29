import { useRef, useState } from "react"
import { Link } from "react-router"
import {
  Play, Pause, SkipBack, SkipForward,
  Shuffle, Repeat1, Infinity, Volume2, VolumeX, Volume1,
  ChevronUp, ThumbsUp, ThumbsDown, MoreHorizontal,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { usePlayerStore } from "@/store/playerStore"
import { useNavidromeStore } from "@/store/navidromeStore"
import ArtworkImage from "@/components/media/ArtworkImage"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuTrigger, DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu"

function formatTime(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00"
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, "0")}`
}

function VolumeControl({
  volume,
  muted,
  onToggleMute,
  onVolumeClick,
}: {
  volume: number
  muted: boolean
  onToggleMute: () => void
  onVolumeClick: (e: React.MouseEvent<HTMLDivElement>) => void
}) {
  const [hovered, setHovered] = useState(false)
  const effective = muted ? 0 : volume

  return (
    <div
      className="flex items-center gap-1"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        onClick={onToggleMute}
        className="p-1.5 text-muted-foreground hover:text-foreground transition-colors"
      >
        {effective === 0 ? <VolumeX size={16} /> : effective < 0.5 ? <Volume1 size={16} /> : <Volume2 size={16} />}
      </button>
      <div
        className={cn(
          "overflow-hidden transition-all duration-200",
          hovered ? "w-20 opacity-100" : "w-0 opacity-0"
        )}
      >
        <div
          className="w-20 h-1 bg-muted rounded cursor-pointer relative group/vol"
          onClick={onVolumeClick}
        >
          <div className="h-full bg-foreground rounded" style={{ width: `${effective * 100}%` }} />
          <div
            className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full bg-foreground opacity-0 group-hover/vol:opacity-100 transition-opacity"
            style={{ left: `calc(${effective * 100}% - 5px)` }}
          />
        </div>
      </div>
    </div>
  )
}

export default function PlayerBar() {
  const currentTrack    = usePlayerStore((s) => s.currentTrack)
  const playbackState   = usePlayerStore((s) => s.playbackState)
  const currentTime     = usePlayerStore((s) => s.currentTime)
  const duration        = usePlayerStore((s) => s.duration)
  const volume          = usePlayerStore((s) => s.volume)
  const muted           = usePlayerStore((s) => s.muted)
  const session         = usePlayerStore((s) => s.session)
  const togglePlay      = usePlayerStore((s) => s.togglePlay)
  const skipNext        = usePlayerStore((s) => s.skipNext)
  const skipPrevious    = usePlayerStore((s) => s.skipPrevious)
  const seek            = usePlayerStore((s) => s.seek)
  const setVolume       = usePlayerStore((s) => s.setVolume)
  const toggleMute      = usePlayerStore((s) => s.toggleMute)
  const toggleShuffle   = usePlayerStore((s) => s.toggleShuffle)
  const toggleRepeatOne = usePlayerStore((s) => s.toggleRepeatOne)
  const toggleAutoplay  = usePlayerStore((s) => s.toggleAutoplay)
  const toggleExpanded  = usePlayerStore((s) => s.toggleExpanded)

  const currentTrackId  = usePlayerStore((s) => s.currentTrackId)
  const toggleLike      = useNavidromeStore((s) => s.toggleLike)
  const liked           = useNavidromeStore((s) => currentTrackId ? s.likedIds.has(currentTrackId) : false)

  const isPlaying  = playbackState === "playing"
  const isLoading  = playbackState === "loading"
  const progress   = duration > 0 ? currentTime / duration : 0

  const shuffle    = session?.shuffle_enabled ?? false
  const repeatOne  = session?.repeat_mode === "one"
  const autoplay   = session?.autoplay_enabled ?? false

  const seekBarRef = useRef<HTMLDivElement>(null)

  function handleSeekClick(e: React.MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    seek(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)))
  }

  function handleVolumeClick(e: React.MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    setVolume(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)))
  }

  const iconBtn = "p-1.5 rounded transition-colors text-muted-foreground hover:text-foreground disabled:opacity-30"
  const activeBtn = "text-primary hover:text-primary"

  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 flex flex-col bg-card border-t border-border">
      {/* Seek bar — top edge */}
      <div
        className="h-1 w-full bg-muted cursor-pointer group/seek relative shrink-0"
        onClick={handleSeekClick}
        ref={seekBarRef}
      >
        <div
          className="h-full bg-primary transition-[width] duration-100"
          style={{ width: `${progress * 100}%` }}
        />
        <div
          className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-primary opacity-0 group-hover/seek:opacity-100 transition-opacity"
          style={{ left: `calc(${progress * 100}% - 6px)` }}
        />
      </div>

      {/* Main row */}
      <div className="h-[72px] flex items-center px-4 gap-2">

        {/* ── LEFT: transport + time ── */}
        <div className="flex items-center gap-1 shrink-0">
          <button onClick={() => skipPrevious()} disabled={!currentTrack} className={iconBtn}>
            <SkipBack size={18} />
          </button>

          <button
            onClick={togglePlay}
            disabled={!currentTrack}
            className="w-8 h-8 rounded-full bg-foreground text-background flex items-center justify-center hover:scale-105 transition-transform disabled:opacity-30 shrink-0 mx-1"
          >
            {isLoading ? (
              <span className="w-3.5 h-3.5 border-2 border-background border-t-transparent rounded-full animate-spin" />
            ) : isPlaying ? (
              <Pause size={15} fill="currentColor" strokeWidth={0} />
            ) : (
              <Play size={15} fill="currentColor" strokeWidth={0} className="translate-x-px" />
            )}
          </button>

          <button onClick={() => skipNext()} disabled={!currentTrack} className={iconBtn}>
            <SkipForward size={18} />
          </button>

          <span className="text-xs text-muted-foreground tabular-nums ml-2 whitespace-nowrap hidden md:inline">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>
        </div>

        {/* ── CENTER: artwork + track info + actions ── */}
        <div className="flex items-center gap-3 flex-1 min-w-0 justify-center">
          {/* Artwork */}
          <div className="w-10 h-10 rounded shrink-0 overflow-hidden">
            <ArtworkImage
              src={currentTrack?.artwork?.url}
              alt={currentTrack?.title ?? ""}
              size={40}
              className="w-10 h-10"
              fallbackLetter={currentTrack?.title?.[0]}
            />
          </div>

          {/* Track info */}
          <div className="min-w-0 text-center">
            {currentTrack ? (
              <>
                <p className="text-sm font-medium truncate leading-tight">
                  {currentTrack.release ? (
                    <Link
                      to={`/releases/${currentTrack.release.id}`}
                      className="hover:underline"
                    >
                      {currentTrack.title}
                    </Link>
                  ) : (
                    currentTrack.title
                  )}
                </p>
                <p className="text-xs text-muted-foreground truncate leading-tight mt-0.5">
                  {currentTrack.artists?.map((a, i) => (
                    <span key={a.id}>
                      {i > 0 && ", "}
                      <Link to={`/artists/${a.id}`} className="hover:underline hover:text-foreground">
                        {a.name}
                      </Link>
                    </span>
                  ))}
                  {currentTrack.release && (
                    <>
                      {" · "}
                      <Link
                        to={`/releases/${currentTrack.release.id}`}
                        className="hover:underline hover:text-foreground"
                      >
                        {currentTrack.release.title}
                      </Link>
                    </>
                  )}
                </p>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">Nothing playing</p>
            )}
          </div>

          {/* Like / Dislike / More */}
          {currentTrackId && (
            <div className="flex items-center gap-0.5 shrink-0">
              <button
                onClick={() => toggleLike(currentTrackId)}
                className={cn(iconBtn, liked && activeBtn)}
                title="Like"
              >
                <ThumbsUp size={15} />
              </button>
              <button
                onClick={() => currentTrackId && toggleLike(currentTrackId)}
                className={cn(iconBtn, "hidden md:flex")}
                title="Dislike"
              >
                <ThumbsDown size={15} />
              </button>
              <div className="hidden md:block">
                <TrackMoreMenu trackId={currentTrackId} />
              </div>
            </div>
          )}
        </div>

        {/* ── RIGHT: volume + shuffle + repeat + autoplay + expand ── */}
        <div className="flex items-center gap-0.5 shrink-0">
          <div className="hidden md:flex items-center">
            <VolumeControl
              volume={volume}
              muted={muted}
              onToggleMute={toggleMute}
              onVolumeClick={handleVolumeClick}
            />
          </div>

          <button
            onClick={() => toggleRepeatOne()}
            className={cn(iconBtn, repeatOne && activeBtn, "hidden md:flex")}
            title="Repeat one"
          >
            <Repeat1 size={16} />
          </button>

          <button
            onClick={() => toggleShuffle()}
            className={cn(iconBtn, shuffle && activeBtn, "hidden md:flex")}
            title="Shuffle"
          >
            <Shuffle size={16} />
          </button>

          <button
            onClick={() => toggleAutoplay()}
            className={cn(iconBtn, autoplay && activeBtn, "hidden md:flex")}
            title="Autoplay"
          >
            <Infinity size={16} />
          </button>

          <button
            onClick={toggleExpanded}
            className={iconBtn}
            title="Expand player"
          >
            <ChevronUp size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}

function TrackMoreMenu({ trackId }: { trackId: number }) {
  const recordEvent = usePlayerStore((s) => s.recordEvent)

  async function handleInstantMix() {
    const { apiFetch } = await import("@/api/client")
    const { usePlayerStore: store } = await import("@/store/playerStore")
    try {
      const envelope = await apiFetch<import("@/api/types").PlaybackEnvelope>(
        `/tracks/${trackId}/instant-mix`,
        { method: "POST" }
      )
      await store.getState().playFromEnvelope(envelope)
    } catch {
      // ignore
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="p-1.5 rounded transition-colors text-muted-foreground hover:text-foreground">
          <MoreHorizontal size={15} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem onClick={handleInstantMix}>
          Instant mix
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => recordEvent("disliked")}>
          Don't play this
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
