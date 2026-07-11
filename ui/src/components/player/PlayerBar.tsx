import { useEffect, useRef, useState, type ReactNode } from "react"
import { Link } from "react-router"
import {
  Play, Pause, SkipBack, SkipForward,
  Shuffle, Repeat1, Infinity, Volume2, VolumeX, Volume1,
  ChevronUp, ThumbsUp, ThumbsDown, MoreHorizontal,
} from "lucide-react"
import type { TrackSummary } from "@/api/types"
import { cn } from "@/lib/utils"
import { usePlayerStore } from "@/store/playerStore"
import { useNavidromeStore } from "@/store/navidromeStore"
import ArtworkImage from "@/components/media/ArtworkImage"
import PlayerBackdrop from "@/components/player/PlayerBackdrop.tsx"
import styles from "./PlayerBar.module.css"
import { readTrackAccentTransitionDurationMs } from "./plasmaUtils.ts"
import { preloadArtworkImage } from "./playerBarTransitionUtils.ts"
import { SeekIndicators, TimeReadout } from "@/components/player/PlaybackProgress"
import { useDragSlider } from "@/components/player/useDragSlider"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuTrigger, DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from "@/components/ui/tooltip"

interface PlayerBarTrackSnapshot {
  key: string | undefined
  track: TrackSummary | null
  trackId: number | null
}

function buildTrackSnapshot(
  track: TrackSummary | null,
  trackId: number | null
): PlayerBarTrackSnapshot {
  return {
    key: trackId ? String(trackId) : undefined,
    track,
    trackId,
  }
}

function SkipPreview({
  track,
  children,
}: {
  readonly track: TrackSummary | null
  readonly children: ReactNode
}) {
  // Portaled (not absolute-in-flow) so the preview escapes the player bar's
  // own `overflow-hidden` instead of being clipped by it.
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        {track && (
          <TooltipContent
            side="top"
            sideOffset={10}
            showArrow={false}
            className="rounded-lg border border-border/40 bg-background/70 backdrop-blur-md px-2.5 py-2 text-foreground shadow-lg"
          >
            <div className="flex items-center gap-2">
              <div className="w-9 h-9 rounded shrink-0 overflow-hidden">
                <ArtworkImage
                  src={track.artwork?.url}
                  alt={track.title}
                  size={36}
                  className="w-9 h-9"
                  fallbackLetter={track.title?.[0]}
                />
              </div>
              <div className="min-w-0 text-left">
                <p className="text-xs font-medium truncate max-w-[160px]">{track.title}</p>
                <p className="text-[11px] text-muted-foreground truncate max-w-[160px]">
                  {track.artists?.map((a) => a.name).join(", ")}
                </p>
              </div>
            </div>
          </TooltipContent>
        )}
      </Tooltip>
    </TooltipProvider>
  )
}

function VolumeControl({
  volume,
  muted,
  onToggleMute,
  onVolumeChange,
}: {
  readonly volume: number
  readonly muted: boolean
  readonly onToggleMute: () => void
  readonly onVolumeChange: (value: number) => void
}) {
  const [hovered, setHovered] = useState(false)
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const effective = muted ? 0 : volume
  const { trackRef: sliderRef, handleMouseDown } = useDragSlider({ onChange: onVolumeChange })

  function handleEnter() {
    if (hideTimer.current) clearTimeout(hideTimer.current)
    setHovered(true)
  }

  function handleLeave() {
    hideTimer.current = setTimeout(() => setHovered(false), 1000)
  }

  return (
    <div
      className="relative flex items-center"
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
      onClick={(e) => e.stopPropagation()}
    >
      {/* Invisible extended hit area covering the slider zone */}
      <div className="absolute right-0 top-1/2 -translate-y-1/2 h-10 w-32" />

      {/* Slider floats to the left, outside of layout flow */}
      <div
        className={cn(
          "absolute right-full mr-2 transition-all duration-200 origin-right",
          hovered ? "opacity-100 translate-x-0 scale-x-100 pointer-events-auto" : "opacity-0 translate-x-3 scale-x-0 pointer-events-none"
        )}
      >
        <div
          ref={sliderRef}
          className="w-20 h-4 flex items-center cursor-pointer relative group/vol"
          onMouseDown={handleMouseDown}
        >
          <div className="w-full h-1 bg-muted rounded relative">
            <div className="h-full bg-foreground rounded" style={{ width: `${effective * 100}%` }} />
            <div
              className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full bg-foreground opacity-0 group-hover/vol:opacity-100 transition-opacity"
              style={{ left: `calc(${effective * 100}% - 5px)` }}
            />
          </div>
        </div>
      </div>
      <button
        onClick={onToggleMute}
        className="p-1.5 text-muted-foreground hover:text-foreground transition-colors relative z-10"
      >
        {effective === 0 ? <VolumeX size={16} /> : effective < 0.5 ? <Volume1 size={16} /> : <Volume2 size={16} />}
      </button>
    </div>
  )
}

export default function PlayerBar() {
  const currentTrack    = usePlayerStore((s) => s.currentTrack)
  const playbackState   = usePlayerStore((s) => s.playbackState)
  const volume          = usePlayerStore((s) => s.volume)
  const muted           = usePlayerStore((s) => s.muted)
  const session         = usePlayerStore((s) => s.session)
  const queue           = usePlayerStore((s) => s.queue)
  const playedHistory   = usePlayerStore((s) => s.playedHistory)
  const currentQueueItemId = usePlayerStore((s) => s.currentQueueItemId)
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

  const shuffle    = session?.shuffle_enabled ?? false
  const repeatOne  = session?.repeat_mode === "one"
  const autoplay   = session?.autoplay_enabled ?? false

  const queueItems  = queue?.items ?? []
  const queueIdx    = queueItems.findIndex((i) => i.id === currentQueueItemId)
  const nextTrack   = queueIdx >= 0 ? queueItems[queueIdx + 1]?.track ?? null : null
  // queue.items only holds current + upcoming, so the previous track has to
  // come from playedHistory (the client-side trail) instead of items[idx-1].
  const prevTrack   = playedHistory.at(-1)?.track ?? null

  const [dragProgress, setDragProgress] = useState<number | null>(null)
  const [visibleSnapshot, setVisibleSnapshot] = useState<PlayerBarTrackSnapshot>(
    buildTrackSnapshot(currentTrack, currentTrackId)
  )
  const [trackDetailsHidden, setTrackDetailsHidden] = useState(false)
  const visibleTrackKeyRef = useRef(visibleSnapshot.key)

  useEffect(() => {
    const nextSnapshot = buildTrackSnapshot(currentTrack, currentTrackId)
    if (nextSnapshot.key === visibleTrackKeyRef.current) {
      setVisibleSnapshot(nextSnapshot)
      setTrackDetailsHidden(false)
      return
    }

    let cancelled = false
    let showFrame: number | undefined
    let swapTimeout: number | undefined
    const reducedMotion = globalThis.matchMedia("(prefers-reduced-motion: reduce)")

    void preloadArtworkImage(nextSnapshot.track?.artwork?.url).then(() => {
      if (cancelled) return

      const swapTrack = () => {
        if (cancelled) return
        visibleTrackKeyRef.current = nextSnapshot.key
        setVisibleSnapshot(nextSnapshot)
        showFrame = globalThis.requestAnimationFrame(() => {
          if (!cancelled) setTrackDetailsHidden(false)
        })
      }

      if (reducedMotion.matches) {
        swapTrack()
        return
      }

      setTrackDetailsHidden(true)
      swapTimeout = globalThis.setTimeout(
        swapTrack,
        readTrackAccentTransitionDurationMs() / 2
      )
    })

    return () => {
      cancelled = true
      if (swapTimeout !== undefined) globalThis.clearTimeout(swapTimeout)
      if (showFrame !== undefined) globalThis.cancelAnimationFrame(showFrame)
    }
  }, [currentTrack, currentTrackId])

  const { trackRef: seekBarRef, handleMouseDown: handleSeekMouseDown } = useDragSlider({
    onChange: setDragProgress,
    onCommit: (v) => {
      seek(v)
      setDragProgress(null)
    },
  })

const iconBtn = "p-1.5 rounded transition-colors text-muted-foreground hover:text-foreground disabled:opacity-30"
  const skipBtn = "p-1.5 rounded-full transition-colors text-muted-foreground hover:text-foreground hover:bg-muted/60 disabled:opacity-30 disabled:hover:bg-transparent"
  const activeBtn = "text-primary hover:text-primary"

  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 isolate flex flex-col overflow-hidden bg-card border-t border-white/10 shadow-[0_-10px_30px_rgb(0_0_0/0.18)]">
      <PlayerBackdrop
        artworkUrl={currentTrack?.artwork?.url}
        isPlaying={isPlaying}
      />

      {/* Seek bar — top edge. Outer div is an oversized hit area (thin visual
          line + thumb sit inside it) so the bar is easy to grab and the thumb
          — centered on the box — has room to render without being clipped by
          the player's outer `overflow-hidden`. */}
      <div
        className="relative z-10 h-3 w-full cursor-pointer group/seek shrink-0"
        onMouseDown={handleSeekMouseDown}
        ref={seekBarRef}
      >
        <div className="absolute top-1/2 -translate-y-1/2 left-0 right-0 h-1 bg-muted/75" />
        <SeekIndicators
          override={dragProgress}
          bufferedClassName="absolute top-1/2 -translate-y-1/2 left-0 h-1 bg-foreground/25"
          fillClassName={cn(
            "absolute top-1/2 -translate-y-1/2 left-0 h-1 bg-primary",
            dragProgress === null && "transition-[width] duration-100",
          )}
          thumbClassName="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-primary opacity-0 group-hover/seek:opacity-100 transition-opacity"
        />
      </div>

      {/* Main row */}
      <div
        className="relative z-10 h-[72px] flex items-center px-4 gap-2 cursor-pointer"
        onClick={(e) => {
          if ((e.target as HTMLElement).closest("button, a, [role='switch']")) return
          toggleExpanded()
        }}
      >

        {/* ── LEFT: transport + time ── */}
        <div className="flex items-center gap-1 shrink-0">
          <SkipPreview track={prevTrack}>
            <button onClick={() => skipPrevious()} disabled={!currentTrack} className={skipBtn} title="Previous track">
              <SkipBack size={18} />
            </button>
          </SkipPreview>

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

          <SkipPreview track={nextTrack}>
            <button onClick={() => skipNext()} disabled={!currentTrack} className={skipBtn} title="Next track">
              <SkipForward size={18} />
            </button>
          </SkipPreview>

          <TimeReadout
            variant="inline"
            className="text-xs text-muted-foreground tabular-nums ml-2 whitespace-nowrap hidden md:inline"
          />
        </div>

        {/* ── CENTER: artwork + track info + actions ── */}
        <div className={styles.trackStack}>
          <div
            className={cn(
              styles.trackLayer,
              trackDetailsHidden && styles.trackLayerHidden
            )}
          >
            <TrackDetails
              snapshot={visibleSnapshot}
              iconBtn={iconBtn}
              activeBtn={activeBtn}
              toggleLike={toggleLike}
              liked={liked}
            />
          </div>
        </div>

        {/* ── RIGHT: volume + shuffle + repeat + autoplay + expand ── */}
        <div className="flex items-center gap-0.5 shrink-0">
          <div className="hidden md:flex items-center">
            <VolumeControl
              volume={volume}
              muted={muted}
              onToggleMute={toggleMute}
              onVolumeChange={setVolume}
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

function TrackMoreMenu({ trackId }: { readonly trackId: number }) {
  const recordEvent = usePlayerStore((s) => s.recordEvent)

  async function handleInstantMix() {
    const { apiFetch } = await import("@/api/client")
    const { usePlayerStore: store } = await import("@/store/playerStore")
    try {
      const envelope = await apiFetch<import("@/api/types").PlaybackEnvelope>(
        `/api/v1/tracks/${trackId}/instant-mix`,
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

function TrackDetails({
  snapshot,
  iconBtn,
  activeBtn,
  toggleLike,
  liked,
}: {
  readonly snapshot: PlayerBarTrackSnapshot
  readonly iconBtn: string
  readonly activeBtn: string
  readonly toggleLike: (trackId: number) => void
  readonly liked: boolean
}) {
  const { track, trackId } = snapshot

  return (
    <div className="flex items-center gap-3 w-full min-w-0 justify-center">
      <div className="w-10 h-10 rounded shrink-0 overflow-hidden">
        <ArtworkImage
          key={track?.artwork?.url ?? trackId ?? "empty"}
          src={track?.artwork?.url}
          alt={track?.title ?? ""}
          size={40}
          className="w-10 h-10"
          fallbackLetter={track?.title?.[0]}
        />
      </div>

      <div className="min-w-0 text-left">
        {track ? (
          <>
            <p className="text-sm font-medium truncate leading-tight">
              {track.release ? (
                <Link
                  to={`/releases/${track.release.id}`}
                  className="hover:underline"
                >
                  {track.title}
                </Link>
              ) : (
                track.title
              )}
            </p>
            <p className="text-xs text-muted-foreground truncate leading-tight mt-0.5">
              {track.artists?.map((artist, index) => (
                <span key={artist.id}>
                  {index > 0 && ", "}
                  <Link to={`/artists/${artist.id}`} className="hover:underline hover:text-foreground">
                    {artist.name}
                  </Link>
                </span>
              ))}
              {track.release && (
                <>
                  {" В· "}
                  <Link
                    to={`/releases/${track.release.id}`}
                    className="hover:underline hover:text-foreground"
                  >
                    {track.release.title}
                  </Link>
                </>
              )}
            </p>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Nothing playing</p>
        )}
      </div>

      {trackId && (
        <div className="flex items-center gap-0.5 shrink-0">
          <button
            onClick={() => toggleLike(trackId)}
            className={cn(iconBtn, liked && activeBtn)}
            title="Like"
          >
            <ThumbsUp size={15} />
          </button>
          <button
            onClick={() => toggleLike(trackId)}
            className={cn(iconBtn, "hidden md:flex")}
            title="Dislike"
          >
            <ThumbsDown size={15} />
          </button>
          <div className="hidden md:block">
            <TrackMoreMenu trackId={trackId} />
          </div>
        </div>
      )}
    </div>
  )
}
