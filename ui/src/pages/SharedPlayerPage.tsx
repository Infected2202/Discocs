import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react"
import { Loader2, Pause, Play, Repeat1, RotateCcw, SkipBack, SkipForward, Volume2, VolumeX } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Link, useParams } from "react-router"
import { fetchPublicShare, type PublicShare } from "@/api/shares"
import ArtworkImage from "@/components/media/ArtworkImage"
import { useDragSlider } from "@/components/player/useDragSlider"
import ShareDownloadMenu from "@/components/share/ShareDownloadMenu"
import { useArtworkTheme } from "@/hooks/useArtworkTheme"
import { cn } from "@/lib/utils"

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00"
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`
}

/**
 * The element's own `duration` is unusable while a transcoded stream arrives
 * without a declared length — the browser reports Infinity for the whole
 * track. Infinity is truthy, so a plain `reported || fallback` would adopt it
 * and freeze progress at `t / Infinity === 0` while making every seek target
 * `fraction * Infinity`. Prefer whatever the element actually resolved, and
 * fall back to the duration the share API already told us.
 */
function resolveDuration(reported: number | undefined, fromApi: number | null | undefined): number {
  if (reported !== undefined && Number.isFinite(reported) && reported > 0) return reported
  if (fromApi != null && Number.isFinite(fromApi) && fromApi > 0) return fromApi
  return 0
}

export default function SharedPlayerPage() {
  const { t } = useTranslation("share")
  const { token = "" } = useParams<{ token: string }>()
  const audioRef = useRef<HTMLAudioElement>(null)
  const autoplayNextRef = useRef(false)
  const durationRef = useRef(0)
  const [share, setShare] = useState<PublicShare | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [buffering, setBuffering] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [dragProgress, setDragProgress] = useState<number | null>(null)
  const [volume, setVolume] = useState(1)
  const [muted, setMuted] = useState(false)
  const [repeatOne, setRepeatOne] = useState(false)
  const [mobileQueue, setMobileQueue] = useState(false)

  useArtworkTheme(share?.artwork_url ?? null)

  const item = share?.items[index]
  const availableIndexes = useMemo(
    () => share?.items.map((entry, position) => entry.available ? position : -1).filter((position) => position >= 0) ?? [],
    [share],
  )

  const commitSeek = (fraction: number) => {
    const audio = audioRef.current
    const knownDuration = durationRef.current
    if (audio && Number.isFinite(knownDuration) && knownDuration > 0) {
      const nextTime = fraction * knownDuration
      audio.currentTime = nextTime
      setCurrentTime(nextTime)
    }
    setDragProgress(null)
  }
  const { trackRef: seekBarRef, handlePointerDown: handleSeekPointerDown } = useDragSlider({
    onChange: setDragProgress,
    onCommit: commitSeek,
  })

  useEffect(() => {
    const referrer = document.createElement("meta")
    referrer.name = "referrer"
    referrer.content = "no-referrer"
    const robots = document.createElement("meta")
    robots.name = "robots"
    robots.content = "noindex,nofollow,noarchive"
    document.head.append(referrer, robots)
    return () => {
      referrer.remove()
      robots.remove()
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(false)
    fetchPublicShare(token)
      .then((payload) => {
        if (cancelled) return
        setShare(payload)
        const firstAvailable = payload.items.findIndex((entry) => entry.available)
        setIndex(Math.max(firstAvailable, 0))
      })
      .catch(() => {
        if (!cancelled) setError(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [token])

  useEffect(() => {
    const audio = audioRef.current
    if (!audio || !item?.available) return
    audio.pause()
    audio.src = item.audio_url
    audio.load()
    setPlaying(false)
    setCurrentTime(0)
    durationRef.current = resolveDuration(undefined, item.duration)
    setDuration(durationRef.current)
    setDragProgress(null)
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: item.title,
        artist: item.artist ?? share?.subtitle ?? "",
        album: share?.title ?? "",
        artwork: share?.artwork_url ? [{ src: share.artwork_url }] : [],
      })
    }
    if (autoplayNextRef.current) {
      autoplayNextRef.current = false
      void audio.play().catch(() => setPlaying(false))
    }
  }, [item?.audio_url, item?.available, item?.artist, item?.duration, item?.title, share?.artwork_url, share?.subtitle, share?.title])

  useEffect(() => () => {
    const audio = audioRef.current
    if (audio) {
      audio.pause()
      audio.removeAttribute("src")
      audio.load()
    }
    if ("mediaSession" in navigator) navigator.mediaSession.metadata = null
  }, [])

  function nextAvailable(direction: 1 | -1): number | null {
    if (!share || availableIndexes.length === 0) return null
    const current = availableIndexes.indexOf(index)
    if (current < 0) return availableIndexes[0]
    const target = current + direction
    if (target < 0 || target >= availableIndexes.length) return null
    return availableIndexes[target]
  }

  async function playAt(target: number) {
    if (!share?.items[target]?.available) return
    autoplayNextRef.current = true
    setIndex(target)
    setMobileQueue(false)
  }

  async function togglePlay() {
    const audio = audioRef.current
    if (!audio || !item?.available) return
    if (audio.paused) await audio.play()
    else audio.pause()
  }

  function handleEnded() {
    const audio = audioRef.current
    if (repeatOne && audio) {
      audio.currentTime = 0
      void audio.play()
      return
    }
    const next = nextAvailable(1)
    if (next === null) {
      setPlaying(false)
      return
    }
    void playAt(next)
  }

  useEffect(() => {
    if (!("mediaSession" in navigator)) return
    const handlers: Array<[MediaSessionAction, MediaSessionActionHandler]> = [
      ["play", () => { void audioRef.current?.play() }],
      ["pause", () => audioRef.current?.pause()],
      ["nexttrack", () => {
        const target = nextAvailable(1)
        if (target !== null) void playAt(target)
      }],
      ["previoustrack", () => {
        const target = nextAvailable(-1)
        if (target !== null) void playAt(target)
      }],
    ]
    for (const [action, handler] of handlers) {
      try { navigator.mediaSession.setActionHandler(action, handler) } catch { /* unsupported */ }
    }
    return () => {
      for (const [action] of handlers) {
        try { navigator.mediaSession.setActionHandler(action, null) } catch { /* unsupported */ }
      }
    }
  }, [availableIndexes, index, share])

  if (loading) {
    return <main className="min-h-dvh grid place-items-center bg-background text-foreground"><Loader2 className="animate-spin" /></main>
  }
  if (error || !share || availableIndexes.length === 0) {
    return (
      <main className="min-h-dvh grid place-items-center bg-background px-6 text-center text-foreground">
        <div className="space-y-4">
          <RotateCcw size={32} className="mx-auto text-muted-foreground" />
          <h1 className="text-xl font-semibold">{t("unavailableTitle")}</h1>
          <p className="max-w-sm text-sm text-muted-foreground">{t("unavailableDescription")}</p>
          <button className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground" onClick={() => location.reload()}>{t("retry")}</button>
        </div>
      </main>
    )
  }

  const previous = nextAvailable(-1)
  const next = nextAvailable(1)
  const progressFraction = duration > 0 ? Math.min(1, currentTime / duration) : 0
  const displayedProgress = dragProgress ?? progressFraction
  const displayedTime = dragProgress === null ? currentTime : dragProgress * duration

  return (
    <main className="relative min-h-dvh overflow-hidden bg-background text-foreground">
      {share.artwork_url && (
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <img src={share.artwork_url} alt="" className="h-full w-full scale-110 object-cover opacity-35 blur-2xl" />
          <div
            className="absolute inset-0 opacity-70"
            style={{ background: "radial-gradient(circle at 35% 30%, color-mix(in srgb, var(--track-accent) 38%, transparent), transparent 62%)" }}
          />
          <div className="absolute inset-0 bg-gradient-to-b from-background/45 via-background/80 to-background" />
        </div>
      )}
      <audio
        ref={audioRef}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onWaiting={() => setBuffering(true)}
        onPlaying={() => setBuffering(false)}
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        onDurationChange={(event) => {
          durationRef.current = resolveDuration(event.currentTarget.duration, item?.duration)
          setDuration(durationRef.current)
        }}
        onEnded={handleEnded}
        onError={() => setBuffering(false)}
      />

      <div className="relative z-10 flex min-h-dvh flex-col md:flex-row">
        <section className={cn("flex min-w-0 flex-1 flex-col", mobileQueue && "hidden md:flex")}>
          <header className="flex items-center justify-between px-5 py-4">
            <Link
              to="/"
              aria-label="Discocs"
              className="text-lg font-bold tracking-tight text-primary transition-colors hover:text-primary/80"
              style={{ fontFamily: "'Onest Variable', sans-serif" }}
            >
              discocs
            </Link>
            <div className="flex items-center gap-2">
              {share.items.length > 1 && <button className="rounded-md bg-muted px-3 py-1.5 text-sm md:hidden" onClick={() => setMobileQueue(true)}>{t("queue")}</button>}
              {item?.available && (
                <ShareDownloadMenu
                  scope="track"
                  label={item.title}
                  downloadUrl={item.download_url}
                />
              )}
            </div>
          </header>

          <div className="flex flex-1 flex-col items-center justify-center gap-7 px-7 py-5">
            <ArtworkImage
              src={share.artwork_url}
              alt={item?.title ?? share.title}
              fallbackLetter={(item?.title ?? share.title)[0]}
              className="aspect-square w-full max-w-[min(56vh,520px)] rounded-xl object-cover shadow-2xl"
            />
            <div className="w-full max-w-xl space-y-5">
              <div className="text-center">
                <h2 className="truncate text-2xl font-bold">{item?.title}</h2>
                <p className="truncate text-sm text-muted-foreground">{item?.artist ?? share.subtitle}</p>
              </div>
              <div className="space-y-1.5">
                <div
                  aria-label={t("seek")}
                  aria-valuemin={0}
                  aria-valuemax={Math.round(duration)}
                  aria-valuenow={Math.round(displayedTime)}
                  aria-valuetext={`${formatTime(displayedTime)} / ${formatTime(duration)}`}
                  role="slider"
                  tabIndex={0}
                  ref={seekBarRef}
                  onPointerDown={handleSeekPointerDown}
                  onKeyDown={(event) => {
                    let nextTime = currentTime
                    if (event.key === "ArrowLeft") nextTime = Math.max(0, currentTime - 5)
                    else if (event.key === "ArrowRight") nextTime = Math.min(duration, currentTime + 5)
                    else if (event.key === "Home") nextTime = 0
                    else if (event.key === "End") nextTime = duration
                    else return
                    event.preventDefault()
                    commitSeek(duration > 0 ? nextTime / duration : 0)
                  }}
                  className="group/seek relative h-4 w-full cursor-pointer touch-none"
                >
                  <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded bg-muted" />
                  <div className="absolute left-0 top-1/2 h-1 -translate-y-1/2 rounded bg-primary" style={{ width: `${displayedProgress * 100}%` }} />
                  <div
                    className="share-seek-thumb absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary shadow transition-opacity"
                    data-dragging={dragProgress !== null}
                    style={{ left: `${displayedProgress * 100}%` }}
                  />
                </div>
                <div className="flex justify-between text-xs tabular-nums text-muted-foreground"><span>{formatTime(displayedTime)}</span><span>{formatTime(duration)}</span></div>
              </div>
              <div className="relative flex items-center justify-center gap-5">
                <button aria-label={t("previous")} disabled={previous === null} className="disabled:opacity-30" onClick={() => previous !== null && void playAt(previous)}><SkipBack size={26} /></button>
                <button aria-label={playing ? t("pause") : t("play")} onClick={() => void togglePlay()} className="grid h-16 w-16 place-items-center rounded-full bg-foreground text-background shadow-lg">
                  {buffering ? <Loader2 size={28} className="animate-spin" /> : playing ? <Pause size={28} fill="currentColor" /> : <Play size={28} fill="currentColor" className="ml-1" />}
                </button>
                <button aria-label={t("next")} disabled={next === null} className="disabled:opacity-30" onClick={() => next !== null && void playAt(next)}><SkipForward size={26} /></button>
                <button aria-label={t("repeatOne")} aria-pressed={repeatOne} onClick={() => setRepeatOne((value) => !value)} className={cn("absolute right-0", repeatOne ? "text-primary" : "text-muted-foreground")}><Repeat1 size={19} /></button>
              </div>
              <div className="mx-auto flex max-w-xs items-center gap-3">
                <button aria-label={muted ? t("unmute") : t("mute")} onClick={() => {
                  const nextMuted = !muted
                  setMuted(nextMuted)
                  if (audioRef.current) audioRef.current.muted = nextMuted
                }}>{muted ? <VolumeX size={18} /> : <Volume2 size={18} />}</button>
                <input aria-label={t("volume")} type="range" min={0} max={1} step={0.01} value={volume} onChange={(event) => {
                  const nextVolume = Number(event.target.value)
                  setVolume(nextVolume)
                  if (audioRef.current) audioRef.current.volume = nextVolume
                }}
                  className="share-range w-full"
                  style={{ "--share-range-progress": `${volume * 100}%` } as CSSProperties}
                />
              </div>
            </div>
          </div>
        </section>

        {share.items.length > 1 && (
          <aside className={cn("w-full border-l border-border/60 bg-background/70 backdrop-blur-xl md:flex md:w-[420px] md:flex-col", mobileQueue ? "flex min-h-dvh flex-col" : "hidden")}>
            <header className="flex items-center justify-between gap-2 border-b border-border/60 px-5 py-4">
              <h2 className="font-semibold">{t("queue")}</h2>
              <div className="flex items-center gap-2">
                <button className="text-sm text-muted-foreground md:hidden" onClick={() => setMobileQueue(false)}>{t("player")}</button>
                <ShareDownloadMenu scope="collection" label={share.title} downloadUrl={share.download_url} />
              </div>
            </header>
            <div className="flex-1 overflow-y-auto p-2">
              {share.items.map((entry, position) => (
                <div
                  key={entry.position}
                  className={cn("flex w-full items-center rounded-lg pr-2 transition-colors hover:bg-muted", position === index && "bg-muted")}
                >
                  {/* The row and its menu are siblings, not nested buttons: a
                      button inside a button is invalid and browsers resolve
                      the inner click unpredictably. */}
                  <button
                    aria-label={t("playTrack", { name: entry.title })}
                    disabled={!entry.available}
                    onClick={() => void playAt(position)}
                    className="flex min-w-0 flex-1 items-center gap-3 px-3 py-3 text-left disabled:opacity-35"
                  >
                    <span className="w-6 text-center text-xs tabular-nums text-muted-foreground">{position === index && playing ? "▶" : position + 1}</span>
                    <ArtworkImage
                      src={share.artwork_url}
                      alt={entry.title}
                      fallbackLetter={entry.title[0]}
                      className="h-11 w-11 rounded-md"
                    />
                    <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{entry.title}</span><span className="block truncate text-xs text-muted-foreground">{entry.artist}</span></span>
                    <span className="text-xs tabular-nums text-muted-foreground">{formatTime(entry.duration ?? 0)}</span>
                  </button>
                  {entry.available && (
                    <ShareDownloadMenu
                      scope="track"
                      label={entry.title}
                      downloadUrl={entry.download_url}
                    />
                  )}
                </div>
              ))}
            </div>
          </aside>
        )}
      </div>
    </main>
  )
}
