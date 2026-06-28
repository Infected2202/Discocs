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
import type { TrackSummary } from "@/api/types"

function formatTime(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00"
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, "0")}`
}

// Upgrade artwork URL to max backend resolution (le=600)
function hiresUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined
  if (/size=\d+/.test(url)) return url.replace(/size=\d+/, "size=600")
  const sep = url.includes("?") ? "&" : "?"
  return `${url}${sep}size=600`
}

export default function ExpandedPlayer() {
  const expanded           = usePlayerStore((s) => s.expanded)
  const currentTrack       = usePlayerStore((s) => s.currentTrack)
  const currentTrackId     = usePlayerStore((s) => s.currentTrackId)
  const playbackState      = usePlayerStore((s) => s.playbackState)
  const currentTime        = usePlayerStore((s) => s.currentTime)
  const duration           = usePlayerStore((s) => s.duration)
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
  const jumpToQueueItem      = usePlayerStore((s) => s.jumpToQueueItem)
  const jumpToAutoplayItem   = usePlayerStore((s) => s.jumpToAutoplayItem)
  const playFromEnvelope     = usePlayerStore((s) => s.playFromEnvelope)
  const recordEvent        = usePlayerStore((s) => s.recordEvent)

  const isLiked    = useNavidromeStore((s) => s.isLiked)
  const toggleLike = useNavidromeStore((s) => s.toggleLike)

  const isPlaying  = playbackState === "playing"
  const isLoading  = playbackState === "loading"
  const progress   = duration > 0 ? currentTime / duration : 0
  const shuffle    = session?.shuffle_enabled ?? false
  const repeatOne  = session?.repeat_mode === "one"
  const autoplay   = session?.autoplay_enabled ?? false
  const liked      = currentTrackId ? isLiked(currentTrackId) : false
  const effective  = muted ? 0 : volume

  function handleSeekClick(e: React.MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    seek(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)))
  }

  function handleVolumeClick(e: React.MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    setVolume(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)))
  }

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
      {/* Top bar */}
      <div className="flex items-center justify-between px-6 pt-3 pb-1 shrink-0">
        <button onClick={toggleExpanded} className={iconBtn}>
          <ChevronDown size={20} />
        </button>
        <p className="text-sm font-medium text-muted-foreground truncate px-4 max-w-xs">
          {session?.source_label ?? "Now Playing"}
        </p>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className={iconBtn}><MoreHorizontal size={20} /></button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem onClick={handleInstantMix}>Instant mix</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => recordEvent("disliked")}>Don't play this</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Body — flex row */}
      <div className="flex flex-1 min-h-0 gap-0">

        {/* ── LEFT: artwork + controls ── */}
        <div className="flex flex-col items-center flex-1 min-w-0 min-h-0 gap-4 px-8 py-6">

          {/* Artwork — h-full forces square bounded by panel height */}
          <div className="flex-1 min-h-0 w-full max-h-[65%]">
            <div className="h-full aspect-square mx-auto rounded-xl overflow-hidden shadow-2xl">
              <ArtworkImage
                src={hiresUrl(currentTrack?.artwork?.url)}
                alt={currentTrack?.title ?? ""}
                className="w-full h-full object-cover"
                fallbackLetter={currentTrack?.title?.[0]}
              />
            </div>
          </div>

          {/* Track info + like */}
          <div className="w-full max-w-sm">
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
              {currentTrackId && (
                <button
                  onClick={() => toggleLike(currentTrackId)}
                  className={cn("p-1.5 rounded transition-colors shrink-0 mt-0.5",
                    liked ? "text-primary" : "text-muted-foreground hover:text-foreground")}
                >
                  <ThumbsUp size={18} />
                </button>
              )}
            </div>
          </div>

          {/* Seek bar */}
          <div className="w-full max-w-sm space-y-1">
            <div className="h-1 w-full bg-muted rounded cursor-pointer group/seek relative"
              onClick={handleSeekClick}>
              <div className="h-full bg-primary rounded transition-[width] duration-100"
                style={{ width: `${progress * 100}%` }} />
              <div className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-primary opacity-0 group-hover/seek:opacity-100 transition-opacity shadow"
                style={{ left: `calc(${progress * 100}% - 6px)` }} />
            </div>
            <div className="flex justify-between text-xs text-muted-foreground tabular-nums">
              <span>{formatTime(currentTime)}</span>
              <span>{formatTime(duration)}</span>
            </div>
          </div>

          {/* Controls */}
          <div className="flex items-center gap-2">
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
          <div className="flex items-center gap-2 w-full max-w-xs">
            <button onClick={() => toggleAutoplay()} className={cn("p-1 rounded transition-colors shrink-0",
              autoplay ? "text-primary" : "text-muted-foreground hover:text-foreground")}>
              <Infinity size={15} />
            </button>
            <div className="flex-1 flex items-center gap-2">
              <button onClick={toggleMute} className="text-muted-foreground hover:text-foreground shrink-0">
                {effective === 0 ? <VolumeX size={16} /> : effective < 0.5 ? <Volume1 size={16} /> : <Volume2 size={16} />}
              </button>
              <div className="flex-1 h-1.5 bg-muted rounded cursor-pointer relative group/vol" onClick={handleVolumeClick}>
                <div className="h-full bg-foreground/80 rounded" style={{ width: `${effective * 100}%` }} />
                <div className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-foreground opacity-0 group-hover/vol:opacity-100 transition-opacity"
                  style={{ left: `calc(${effective * 100}% - 6px)` }} />
              </div>
            </div>
          </div>
        </div>

        {/* ── RIGHT: queue panel ── */}
        <div className="w-[440px] shrink-0 flex flex-col border-l border-border overflow-hidden">
          {/* Header — source + autoplay toggle */}
          <div className="px-4 py-3 shrink-0 border-b border-border space-y-2">
            {session?.source_label && (
              <p className="text-xs text-muted-foreground">
                Source: <span className="text-foreground font-medium">{session.source_label}</span>
              </p>
            )}
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
                  isCurrent={isCurrent}
                  time={isCurrent ? `${formatTime(currentTime)} / ${formatTime(duration)}` : undefined}
                  onClick={isCurrent ? undefined : () => jumpToQueueItem(item.id)}
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
                    dimmed
                    onClick={() => jumpToAutoplayItem(item.id)}
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
