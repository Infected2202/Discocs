import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react"
import { Loader2, Pause, Play, Repeat1, RotateCcw, SkipBack, SkipForward, Volume2, VolumeX } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Link, useParams } from "react-router"
import { fetchPublicShare, type PublicShare } from "@/api/shares"
import ArtworkImage from "@/components/media/ArtworkImage"
import { useArtworkTheme } from "@/hooks/useArtworkTheme"
import { cn } from "@/lib/utils"

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00"
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`
}

export default function SharedPlayerPage() {
  const { t } = useTranslation("share")
  const { token = "" } = useParams<{ token: string }>()
  const audioRef = useRef<HTMLAudioElement>(null)
  const autoplayNextRef = useRef(false)
  const [share, setShare] = useState<PublicShare | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [buffering, setBuffering] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
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
    setDuration(item.duration ?? 0)
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
  const progress = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0

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
        onDurationChange={(event) => setDuration(event.currentTarget.duration || item?.duration || 0)}
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
            {share.items.length > 1 && <button className="rounded-md bg-muted px-3 py-1.5 text-sm md:hidden" onClick={() => setMobileQueue(true)}>{t("queue")}</button>}
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
                <input
                  aria-label={t("seek")}
                  type="range"
                  min={0}
                  max={1000}
                  value={Math.round(progress * 10)}
                  onChange={(event) => {
                    const audio = audioRef.current
                    if (audio && duration > 0) audio.currentTime = Number(event.target.value) / 1000 * duration
                  }}
                  className="share-range w-full"
                  style={{ "--share-range-progress": `${progress}%` } as CSSProperties}
                />
                <div className="flex justify-between text-xs tabular-nums text-muted-foreground"><span>{formatTime(currentTime)}</span><span>{formatTime(duration)}</span></div>
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
            <header className="flex items-center justify-between border-b border-border/60 px-5 py-4"><h2 className="font-semibold">{t("queue")}</h2><button className="text-sm text-muted-foreground md:hidden" onClick={() => setMobileQueue(false)}>{t("player")}</button></header>
            <div className="flex-1 overflow-y-auto p-2">
              {share.items.map((entry, position) => (
                <button
                  key={entry.position}
                  disabled={!entry.available}
                  onClick={() => void playAt(position)}
                  className={cn("flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left transition-colors hover:bg-muted disabled:opacity-35", position === index && "bg-muted")}
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
              ))}
            </div>
          </aside>
        )}
      </div>
    </main>
  )
}
