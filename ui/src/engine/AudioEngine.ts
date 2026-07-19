import type { TrackSummary } from "@/api/types"

export type PlaybackState = "idle" | "loading" | "playing" | "paused" | "error"
export interface BufferedRange {
  start: number
  end: number
}

interface AudioEngineCallbacks {
  onTimeUpdate(currentTime: number, duration: number): void
  onPlaybackStateChange(state: PlaybackState): void
  onBufferUpdate(ranges: BufferedRange[]): void
  onFullyBuffered?(trackId: number, profileKey: string): void
  onEnded(): void
  onError(message: string): void
}

class AudioEngine {
  private el: HTMLAudioElement
  private callbacks: AudioEngineCallbacks | null = null
  private activeTrackId: number | null = null
  private activeProfileKey = "raw"
  private activeObjectUrl: string | null = null
  private fullyBufferedReported = false
  private prefetched: { trackId: number; profileKey: string; objectUrl: string } | null = null
  private prefetchController: AbortController | null = null
  private prefetchTarget: { trackId: number; profileKey: string } | null = null

  constructor() {
    this.el = this.createElement()
  }

  init(callbacks: AudioEngineCallbacks) {
    this.callbacks = callbacks
  }

  load(
    url: string,
    trackId: number | null = null,
    profileKey = "raw",
    fullyAvailable = false,
  ) {
    // Явно освобождаем буфер старого элемента — src='' надёжнее removeAttribute
    const prev = this.el
    prev.pause()
    prev.src = ""
    prev.load()
    if (this.activeObjectUrl && this.activeObjectUrl !== url) {
      URL.revokeObjectURL(this.activeObjectUrl)
      this.activeObjectUrl = null
    }

    // Новый элемент — Chrome гарантированно освобождает нативный PCM буфер
    // когда старый элемент теряет все ссылки и GC его собирает
    this.el = this.createElement()
    this.el.volume = prev.volume
    this.el.muted = prev.muted
    this.el.src = url
    this.el.load()
    this.activeTrackId = trackId
    this.activeProfileKey = profileKey
    this.fullyBufferedReported = false

    // Reset immediately — otherwise the buffered indicator briefly shows
    // the previous track's ranges. A prepared Blob is already fully local.
    this.callbacks?.onBufferUpdate(fullyAvailable ? [{ start: 0, end: 1 }] : [])
    if (fullyAvailable && trackId !== null) this.reportFullyBuffered()
  }

  async prefetch(trackId: number, url: string, profileKey: string): Promise<void> {
    if (this.prefetched?.trackId === trackId && this.prefetched.profileKey === profileKey) return
    if (this.prefetchTarget?.trackId === trackId && this.prefetchTarget.profileKey === profileKey) return
    this.cancelPrefetch()
    this.clearPrefetched()
    const controller = new AbortController()
    this.prefetchController = controller
    this.prefetchTarget = { trackId, profileKey }
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        signal: controller.signal,
      })
      if (!response.ok) throw new Error(`Audio prefetch failed: HTTP ${response.status}`)
      const blob = await response.blob()
      if (controller.signal.aborted) return
      if (this.prefetchTarget?.trackId !== trackId || this.prefetchTarget.profileKey !== profileKey) return
      this.prefetched = { trackId, profileKey, objectUrl: URL.createObjectURL(blob) }
    } finally {
      if (this.prefetchController === controller) {
        this.prefetchController = null
        this.prefetchTarget = null
      }
    }
  }

  consumePrefetched(trackId: number, profileKey: string): string | null {
    if (this.prefetched?.trackId !== trackId || this.prefetched.profileKey !== profileKey) return null
    const objectUrl = this.prefetched.objectUrl
    this.prefetched = null
    if (this.activeObjectUrl && this.activeObjectUrl !== objectUrl) {
      URL.revokeObjectURL(this.activeObjectUrl)
    }
    this.activeObjectUrl = objectUrl
    return objectUrl
  }

  cancelPrefetch() {
    this.prefetchController?.abort()
    this.prefetchController = null
    this.prefetchTarget = null
  }

  clearPrefetched() {
    if (this.prefetched) URL.revokeObjectURL(this.prefetched.objectUrl)
    this.prefetched = null
  }

  async play(): Promise<void> {
    await this.el.play()
  }

  pause() {
    this.el.pause()
  }

  clear() {
    const prev = this.el
    const { volume, muted } = prev
    prev.pause()
    prev.src = ""
    prev.load()
    this.cancelPrefetch()
    this.clearPrefetched()
    if (this.activeObjectUrl) URL.revokeObjectURL(this.activeObjectUrl)
    this.activeObjectUrl = null
    this.activeTrackId = null
    this.fullyBufferedReported = false

    this.el = this.createElement()
    this.el.volume = volume
    this.el.muted = muted

    this.callbacks?.onTimeUpdate(0, 0)
    this.callbacks?.onBufferUpdate([])
    this.callbacks?.onPlaybackStateChange("idle")

    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = null
      for (const action of ["play", "pause", "nexttrack", "previoustrack"] as MediaSessionAction[]) {
        try {
          navigator.mediaSession.setActionHandler(action, null)
        } catch {
          // browser may not support all actions
        }
      }
    }
  }

  seek(fraction: number) {
    if (!Number.isFinite(this.el.duration)) return
    this.el.currentTime = fraction * this.el.duration
  }

  seekToSeconds(seconds: number) {
    this.el.currentTime = seconds
  }

  /**
   * Seek as soon as the track's metadata is available. Usable right after
   * load(): duration can remain unknown until metadata arrives,
   * so an immediate currentTime write would be silently dropped.
   */
  resumeAtSeconds(seconds: number) {
    if (Number.isFinite(this.el.duration) && this.el.duration > 0) {
      this.el.currentTime = seconds
      return
    }
    const el = this.el
    const apply = () => {
      el.removeEventListener("loadedmetadata", apply)
      el.currentTime = seconds
    }
    el.addEventListener("loadedmetadata", apply)
  }

  setVolume(v: number) {
    this.el.volume = Math.max(0, Math.min(1, v))
  }

  setMuted(muted: boolean) {
    this.el.muted = muted
  }

  get currentTime() {
    return this.el.currentTime
  }

  get duration() {
    return this.el.duration
  }

  get paused() {
    return this.el.paused
  }

  setMediaSession(track: TrackSummary, artworkUrl?: string) {
    if (!("mediaSession" in navigator)) return
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title,
      artist: track.artists.map((a) => a.name).join(", "),
      album: track.release?.title ?? "",
      // No `type` here — Chrome validates the fetched resource's actual
      // Content-Type against a declared one and silently drops the artwork
      // on mismatch, and the backend cover endpoint proxies Navidrome's
      // content-type as-is (jpeg or png depending on the source file).
      artwork: artworkUrl ? [{ src: artworkUrl, sizes: "512x512" }] : [],
    })
  }

  registerMediaSessionHandlers(handlers: {
    play?(): void
    pause?(): void
    nexttrack?(): void
    previoustrack?(): void
  }) {
    if (!("mediaSession" in navigator)) return
    for (const [action, handler] of Object.entries(handlers)) {
      try {
        navigator.mediaSession.setActionHandler(action as MediaSessionAction, handler ?? null)
      } catch {
        // browser may not support all actions
      }
    }
  }

  private createElement(): HTMLAudioElement {
    const el = new Audio()
    el.preload = "auto"
    this.attachListeners(el)
    return el
  }

  private attachListeners(el: HTMLAudioElement) {
    el.addEventListener("timeupdate", () => {
      this.callbacks?.onTimeUpdate(el.currentTime, el.duration || 0)
    })

    const reportBuffered = () => {
      if (el !== this.el) return
      if (!Number.isFinite(el.duration) || el.duration <= 0) return
      // A media element may retain several disjoint ranges after seeking.
      // Preserve every segment so the UI never paints an unloaded gap as
      // downloaded content.
      const ranges: BufferedRange[] = []
      const { buffered } = el
      for (let i = 0; i < buffered.length; i++) {
        ranges.push({
          start: Math.max(0, Math.min(1, buffered.start(i) / el.duration)),
          end: Math.max(0, Math.min(1, buffered.end(i) / el.duration)),
        })
      }
      this.callbacks?.onBufferUpdate(ranges)
      if (this.bufferCoversDuration(el.buffered, el.duration)) this.reportFullyBuffered()
    }

    el.addEventListener("progress", reportBuffered)
    el.addEventListener("loadedmetadata", reportBuffered)
    el.addEventListener("durationchange", reportBuffered)
    el.addEventListener("canplaythrough", reportBuffered)

    el.addEventListener("play", () => {
      this.callbacks?.onPlaybackStateChange("playing")
    })

    // "playing" fires after buffering resumes — fixes spinner stuck after "waiting"
    el.addEventListener("playing", () => {
      this.callbacks?.onPlaybackStateChange("playing")
    })

    el.addEventListener("pause", () => {
      if (!el.ended) this.callbacks?.onPlaybackStateChange("paused")
    })

    el.addEventListener("waiting", () => {
      this.callbacks?.onPlaybackStateChange("loading")
    })

    el.addEventListener("canplay", () => {
      // only emit if we were loading — play/pause events handle the rest
    })

    el.addEventListener("ended", () => {
      this.callbacks?.onEnded()
    })

    el.addEventListener("error", () => {
      const err = el.error
      const msg = err ? `Media error ${err.code}: ${err.message}` : "Unknown audio error"
      this.callbacks?.onPlaybackStateChange("error")
      this.callbacks?.onError(msg)
    })
  }

  private bufferCoversDuration(buffered: TimeRanges, duration: number): boolean {
    if (!Number.isFinite(duration) || duration <= 0 || buffered.length === 0) return false
    const tolerance = 0.25
    let coveredEnd = 0
    for (let i = 0; i < buffered.length; i++) {
      const start = buffered.start(i)
      const end = buffered.end(i)
      if (start > coveredEnd + tolerance) return false
      coveredEnd = Math.max(coveredEnd, end)
      if (coveredEnd >= duration - tolerance) return true
    }
    return false
  }

  private reportFullyBuffered() {
    if (this.fullyBufferedReported || this.activeTrackId === null) return
    this.fullyBufferedReported = true
    this.callbacks?.onFullyBuffered?.(this.activeTrackId, this.activeProfileKey)
  }
}

// Singleton — created once at module load, lives outside React tree
export const audioEngine = new AudioEngine()
