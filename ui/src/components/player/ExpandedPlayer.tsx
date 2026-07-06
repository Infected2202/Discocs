import { useState, useEffect } from "react"
import { Link } from "react-router"
import {
  Play, Pause, SkipBack, SkipForward,
  Shuffle, Repeat1, Infinity, ChevronDown,
  Volume2, VolumeX, Volume1, ThumbsUp, MoreHorizontal,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { usePlayerStore } from "@/store/playerStore"
import { useNavidromeStore } from "@/store/navidromeStore"
import ArtworkImage from "@/components/media/ArtworkImage"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import QueueItem from "@/components/player/QueueItem"
import { SeekIndicators, TimeReadout } from "@/components/player/PlaybackProgress"
import { useDragSlider } from "@/components/player/useDragSlider"
import type { TrackSummary } from "@/api/types"

// Upgrade artwork URL to max backend resolution (le=600)
function hiresUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined
  if (/size=\d+/.test(url)) return url.replace(/size=\d+/, "size=600")
  const sep = url.includes("?") ? "&" : "?"
  return `${url}${sep}size=600`
}

export default function ExpandedPlayer() {
  const [mobileTab, setMobileTab] = useState<"player" | "queue">("player")
  const [artworkPulse, setArtworkPulse] = useState<"play" | "pause" | null>(null)
  const [dragProgress, setDragProgress] = useState<number | null>(null)

  const expanded           = usePlayerStore((s) => s.expanded)
  const currentTrack       = usePlayerStore((s) => s.currentTrack)
  const currentTrackId     = usePlayerStore((s) => s.currentTrackId)
  const playbackState      = usePlayerStore((s) => s.playbackState)
  const volume             = usePlayerStore((s) => s.volume)
  const muted              = usePlayerStore((s) => s.muted)
  const session            = usePlayerStore((s) => s.session)
  const queue              = usePlayerStore((s) => s.queue)
  const currentQueueItemId = usePlayerStore((s) => s.currentQueueItemId)
  const togglePlay         = usePlayerStore((s) => s.togglePlay)
  const skipNext           = usePlayerStore((s) => s.skipNext)
  const skipPrevious       = usePlayerStore((s) => s.skipPrevious)
  const seek               = usePlayerStore((s) => s.seek)
  const setVolume          = usePlayerStore((s) => s.setVolume)
  const toggleMute         = usePlayerStore((s) => s.toggleMute)
  const toggleShuffle      = usePlayerStore((s) => s.toggleShuffle)
  const toggleRepeatOne    = usePlayerStore((s) => s.toggleRepeatOne)
  const toggleAutoplay     = usePlayerStore((s) => s.toggleAutoplay)
  const toggleExpanded     = usePlayerStore((s) => s.toggleExpanded)
  const playFromEnvelope     = usePlayerStore((s) => s.playFromEnvelope)
  const recordEvent        = usePlayerStore((s) => s.recordEvent)

  useEffect(() => {
    if (!expanded) setMobileTab("player")
  }, [expanded])

  const toggleLike = useNavidromeStore((s) => s.toggleLike)
  const liked      = useNavidromeStore((s) => currentTrackId ? s.likedIds.has(currentTrackId) : false)

  const isPlaying  = playbackState === "playing"
  const isLoading  = playbackState === "loading"
  const shuffle    = session?.shuffle_enabled ?? false
  const repeatOne  = session?.repeat_mode === "one"
  const autoplay   = session?.autoplay_enabled ?? false
  const effective  = muted ? 0 : volume

  const { trackRef: seekBarRef, handleMouseDown: handleSeekMouseDown } = useDragSlider({
    onChange: setDragProgress,
    onCommit: (v) => {
      seek(v)
      setDragProgress(null)
    },
  })

  const { trackRef: volumeBarRef, handleMouseDown: handleVolumeMouseDown } = useDragSlider({
    onChange: setVolume,
  })

  async function handleInstantMix() {
    if (!currentTrackId) return
    try {
      const { apiFetch } = await import("@/api/client")
      const envelope = await apiFetch<import("@/api/types").PlaybackEnvelope>(
        `/tracks/${currentTrackId}/instant-mix`,
        { method: "POST" }
      )
      await playFromEnvelope(envelope)
    } catch { /* ignore */ }
  }

  const iconBtn = "p-2 rounded-lg transition-colors text-muted-foreground hover:text-foreground hover:bg-muted/60 disabled:opacity-30"
  const activeBtn = "text-primary hover:text-primary"

  return (
    <div className={cn(
      "fixed inset-0 z-50 bg-background flex flex-col overflow-hidden transition-transform duration-300 ease-out will-change-transform",
      expanded ? "translate-y-0" : "translate-y-full pointer-events-none",
    )}>
      {/* Artwork background */}
      {currentTrack?.artwork?.url && (
        <div className="absolute inset-0 pointer-events-none overflow-hidden">
          <img
            src={hiresUrl(currentTrack.artwork.url)}
            aria-hidden
            className="w-full h-full object-cover object-center"
            style={{ filter: "blur(15px) brightness(0.3) saturate(1.4)", transform: "scale(1.05)", opacity: 0.65 }}
          />
          <div className="absolute inset-0" style={{ background: "linear-gradient(in oklab to bottom, rgb(11 13 15 / 0.3) 0%, rgb(11 13 15 / 0.7) 50%, rgb(11 13 15 / 0.95) 100%)" }} />
        </div>
      )}
      {/* Mobile tab bar */}
      <div className="relative z-10 flex md:hidden items-center px-4 pt-3 pb-1 shrink-0">
        <div className="flex gap-1 bg-muted rounded-lg p-0.5 mr-auto">
          <button
            onClick={() => setMobileTab("player")}
            className={cn("px-3 py-1 text-sm rounded-md transition-colors", mobileTab === "player" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground")}
          >Now Playing</button>
          <button
            onClick={() => setMobileTab("queue")}
            className={cn("px-3 py-1 text-sm rounded-md transition-colors", mobileTab === "queue" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground")}
          >Queue</button>
        </div>
        <button onClick={toggleExpanded} className={iconBtn}>
          <ChevronDown size={20} />
        </button>
      </div>

      {/* Body — flex row on desktop, single column on mobile */}
      <div className="relative z-10 flex flex-1 min-h-0 gap-0">

        {/* ── LEFT: artwork + controls ── */}
        <div
          className={cn(
            "flex flex-col items-center justify-center flex-1 min-w-0 min-h-0 px-8 py-6 cursor-pointer",
            mobileTab === "queue" ? "hidden md:flex" : "flex",
          )}
          onClick={(e) => {
            // collapse if clicking empty space (not interactive children)
            const target = e.target as HTMLElement
            if (target.closest("button, a, [role='switch']")) return
            toggleExpanded()
          }}
        >

          {/* Artwork — on mobile natural square, on desktop fills remaining height */}
          <div className="w-full flex items-center justify-center mb-4">
            <div
              className="relative aspect-square w-full md:w-auto md:max-h-[55vh] md:max-w-[55vh] rounded-xl overflow-hidden shadow-2xl cursor-pointer"
              onClick={(e) => {
                e.stopPropagation()
                togglePlay()
                setArtworkPulse(isPlaying ? "pause" : "play")
                setTimeout(() => setArtworkPulse(null), 350)
              }}
            >
              <ArtworkImage
                src={hiresUrl(currentTrack?.artwork?.url)}
                alt={currentTrack?.title ?? ""}
                className="w-full h-full object-cover"
                fallbackLetter={currentTrack?.title?.[0]}
              />
              {/* Play/pause flash */}
              <div className={cn(
                "absolute inset-0 flex items-center justify-center transition-opacity duration-300",
                artworkPulse ? "opacity-100" : "opacity-0",
              )}>
                <div className="bg-black/50 rounded-full p-5">
                  {artworkPulse === "pause"
                    ? <Pause size={40} fill="white" strokeWidth={0} className="text-white" />
                    : <Play size={40} fill="white" strokeWidth={0} className="text-white ml-1" />
                  }
                </div>
              </div>
            </div>
          </div>

          {/* Bottom group: info + seekbar + controls + volume */}
          <div className="w-full flex flex-col gap-4 shrink-0">

            {/* Track info + like */}
            <div className="w-full max-w-sm mx-auto">
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <h2 className="text-xl font-bold truncate leading-tight">
                    {currentTrack?.title ?? "—"}
                  </h2>
                  <div className="flex flex-wrap items-center gap-x-1 text-sm text-muted-foreground mt-0.5">
                    {currentTrack?.artists?.map((a, i) => (
                      <span key={a.id}>
                        {i > 0 && <span className="mr-0.5">,</span>}
                        <Link to={`/artists/${a.id}`} onClick={toggleExpanded}
                          className="hover:text-foreground hover:underline">{a.name}</Link>
                      </span>
                    ))}
                    {currentTrack?.release && (
                      <>
                        <span className="text-muted-foreground/50">·</span>
                        <Link to={`/releases/${currentTrack.release.id}`} onClick={toggleExpanded}
                          className="hover:text-foreground hover:underline">{currentTrack.release.title}</Link>
                      </>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0 mt-0.5">
                  {currentTrackId && (
                    <button
                      onClick={() => toggleLike(currentTrackId)}
                      className={cn("p-1.5 rounded transition-colors",
                        liked ? "text-primary" : "text-muted-foreground hover:text-foreground")}
                    >
                      <ThumbsUp size={18} />
                    </button>
                  )}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button className={cn(iconBtn, "p-1.5")}><MoreHorizontal size={18} /></button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-44">
                      <DropdownMenuItem onClick={handleInstantMix}>Instant mix</DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onClick={() => recordEvent("disliked")}>Don't play this</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </div>
            </div>

            {/* Seek bar — outer div is an oversized hit area, same pattern as PlayerBar */}
            <div className="w-full max-w-sm mx-auto space-y-1">
              <div
                className="h-4 w-full cursor-pointer group/seek relative"
                onMouseDown={handleSeekMouseDown}
                onClick={(e) => e.stopPropagation()}
                ref={seekBarRef}
              >
                <div className="absolute top-1/2 -translate-y-1/2 left-0 right-0 h-1 bg-muted rounded" />
                <SeekIndicators
                  override={dragProgress}
                  bufferedClassName="absolute top-1/2 -translate-y-1/2 left-0 h-1 bg-foreground/25 rounded"
                  fillClassName={cn(
                    "absolute top-1/2 -translate-y-1/2 left-0 h-1 bg-primary rounded",
                    dragProgress === null && "transition-[width] duration-100",
                  )}
                  thumbClassName="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-primary opacity-0 group-hover/seek:opacity-100 transition-opacity shadow"
                />
              </div>
              <div className="flex justify-between text-xs text-muted-foreground tabular-nums">
                <TimeReadout variant="split" />
              </div>
            </div>

            {/* Controls */}
            <div className="flex items-center gap-2 justify-center">
              <button onClick={() => toggleShuffle()} className={cn(iconBtn, shuffle && activeBtn)}><Shuffle size={18} /></button>
              <button onClick={() => skipPrevious()} disabled={!currentTrack} className={iconBtn}><SkipBack size={22} /></button>
              <button
                onClick={togglePlay}
                disabled={!currentTrack}
                className="w-14 h-14 rounded-full bg-foreground text-background flex items-center justify-center hover:scale-105 transition-transform disabled:opacity-30 shadow-lg mx-1"
              >
                {isLoading
                  ? <span className="w-5 h-5 border-2 border-background border-t-transparent rounded-full animate-spin" />
                  : isPlaying
                    ? <Pause size={22} fill="currentColor" strokeWidth={0} />
                    : <Play size={22} fill="currentColor" strokeWidth={0} className="translate-x-0.5" />
                }
              </button>
              <button onClick={() => skipNext()} disabled={!currentTrack} className={iconBtn}><SkipForward size={22} /></button>
              <button onClick={() => toggleRepeatOne()} className={cn(iconBtn, repeatOne && activeBtn)}><Repeat1 size={18} /></button>
            </div>

            {/* Volume + autoplay */}
            <div className="flex items-center gap-2 w-full max-w-xs mx-auto">
              <button onClick={() => toggleAutoplay()} className={cn("p-1 rounded transition-colors shrink-0",
                autoplay ? "text-primary" : "text-muted-foreground hover:text-foreground")}>
                <Infinity size={15} />
              </button>
              <div className="flex-1 flex items-center gap-2">
                <button onClick={toggleMute} className="text-muted-foreground hover:text-foreground shrink-0">
                  {effective === 0 ? <VolumeX size={16} /> : effective < 0.5 ? <Volume1 size={16} /> : <Volume2 size={16} />}
                </button>
                <div
                  className="flex-1 h-4 cursor-pointer relative group/vol"
                  onMouseDown={handleVolumeMouseDown}
                  onClick={(e) => e.stopPropagation()}
                  ref={volumeBarRef}
                >
                  <div className="absolute top-1/2 -translate-y-1/2 left-0 right-0 h-1.5 bg-muted rounded" />
                  <div className="absolute top-1/2 -translate-y-1/2 left-0 h-1.5 bg-foreground/80 rounded" style={{ width: `${effective * 100}%` }} />
                  <div className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-foreground opacity-0 group-hover/vol:opacity-100 transition-opacity"
                    style={{ left: `calc(${effective * 100}% - 6px)` }} />
                </div>
              </div>
            </div>

          </div>
        </div>

        {/* ── RIGHT: queue panel ── */}
        <div className={cn(
          "flex flex-col border-l border-border overflow-hidden",
          "md:w-[440px] md:shrink-0",
          mobileTab === "queue" ? "flex flex-1 w-full border-l-0" : "hidden md:flex",
        )}>
          {/* Header — collapse + autoplay toggle */}
          <div className="px-4 pt-3 pb-3 shrink-0 border-b border-border">
            <div className="flex justify-end mb-2">
              <button onClick={toggleExpanded} className={iconBtn}>
                <ChevronDown size={20} />
              </button>
            </div>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium">Autoplay</p>
                <p className="text-xs text-muted-foreground leading-tight">Similar tracks added at end of queue</p>
              </div>
              <button
                onClick={() => toggleAutoplay()}
                role="switch"
                aria-checked={autoplay}
                className={cn(
                  "relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200",
                  autoplay ? "bg-primary" : "bg-muted"
                )}
              >
                <span className={cn(
                  "pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-lg ring-0 transition-transform duration-200",
                  autoplay ? "translate-x-5" : "translate-x-0"
                )} />
              </button>
            </div>
          </div>

          {/* Queue list — all items, current highlighted */}
          <div className="flex-1 overflow-y-auto">
            {(queue?.items ?? []).length === 0 && (queue?.autoplay_pool ?? []).length === 0 && (
              <p className="text-sm text-muted-foreground px-5 pt-4">Queue is empty.</p>
            )}

            {queue?.items.map((item) => {
              const isCurrent = item.id === currentQueueItemId
              return (
                <QueueItem
                  key={item.id}
                  track={item.track as TrackSummary | null}
                  trackId={item.track_id}
                  itemId={item.id}
                  variant="queue"
                  isCurrent={isCurrent}
                />
              )
            })}

            {/* Autoplay pool */}
            {(queue?.autoplay_pool ?? []).length > 0 && (
              <>
                <div className="mx-4 mt-3 mb-1 border-t border-border/40" />
                <p className="px-4 py-1 text-xs text-muted-foreground font-medium">Autoplay</p>
                {queue?.autoplay_pool.map((item) => (
                  <QueueItem
                    key={item.id}
                    track={item.track as TrackSummary | null}
                    trackId={item.track_id}
                    itemId={item.id}
                    variant="autoplay"
                    dimmed
                  />
                ))}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
