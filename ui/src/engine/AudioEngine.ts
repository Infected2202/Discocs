import type { TrackSummary } from "@/api/types"
import { playerLog } from "@/lib/playerLogger"

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
  private activeNetworkUrl: string | null = null
  private activeObjectUrl: string | null = null
  private activeCacheController: AbortController | null = null
  private activeCacheTarget: { trackId: number; profileKey: string; url: string } | null = null
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
    this.cancelActiveCache()
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
    this.activeNetworkUrl = fullyAvailable || url.startsWith("blob:") ? null : url
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
    // `preload=auto` is only a browser hint and commonly stalls around 90%.
    // Once playback has actually started, explicitly consume the complete
    // response into a Blob. Native playback remains uninterrupted; the Blob
    // guarantees that all bytes are present before the next prefetch begins.
    this.cacheActiveTrack()
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
    this.cancelActiveCache()
    if (this.activeObjectUrl) URL.revokeObjectURL(this.activeObjectUrl)
    this.activeObjectUrl = null
    this.activeNetworkUrl = null
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
      if (el !== this.el) return
      this.callbacks?.onTimeUpdate(el.currentTime, el.duration || 0)
    })

    const reportBuffered = () => {
      if (el !== this.el) return
      if (this.fullyBufferedReported) {
        this.callbacks?.onBufferUpdate([{ start: 0, end: 1 }])
        return
      }
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
      if (el !== this.el) return
      this.callbacks?.onPlaybackStateChange("playing")
    })

    // "playing" fires after buffering resumes — fixes spinner stuck after "waiting"
    el.addEventListener("playing", () => {
      if (el !== this.el) return
      this.callbacks?.onPlaybackStateChange("playing")
    })

    el.addEventListener("pause", () => {
      if (el !== this.el) return
      if (!el.ended) this.callbacks?.onPlaybackStateChange("paused")
    })

    el.addEventListener("waiting", () => {
      if (el !== this.el) return
      this.callbacks?.onPlaybackStateChange("loading")
    })

    el.addEventListener("canplay", () => {
      // only emit if we were loading — play/pause events handle the rest
    })

    el.addEventListener("ended", () => {
      if (el !== this.el) return
      this.callbacks?.onEnded()
    })

    el.addEventListener("error", () => {
      if (el !== this.el) return
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
    this.cancelActiveCache()
    this.callbacks?.onBufferUpdate([{ start: 0, end: 1 }])
    this.callbacks?.onFullyBuffered?.(this.activeTrackId, this.activeProfileKey)
  }

  private cacheActiveTrack() {
    const trackId = this.activeTrackId
    const profileKey = this.activeProfileKey
    const url = this.activeNetworkUrl
    if (trackId === null || !url || this.fullyBufferedReported) return
    if (
      this.activeCacheTarget?.trackId === trackId
      && this.activeCacheTarget.profileKey === profileKey
      && this.activeCacheTarget.url === url
    ) return

    this.cancelActiveCache()
    const controller = new AbortController()
    const target = { trackId, profileKey, url }
    this.activeCacheController = controller
    this.activeCacheTarget = target
    playerLog("buffer", "force-cache current", { trackId, profile: profileKey })

    void this.fetchAudioBlob(url, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted || this.activeCacheTarget !== target) return
        if (
          this.activeTrackId !== trackId
          || this.activeProfileKey !== profileKey
          || this.activeNetworkUrl !== url
        ) return

        if (this.activeObjectUrl) URL.revokeObjectURL(this.activeObjectUrl)
        this.activeObjectUrl = URL.createObjectURL(blob)
        this.activeNetworkUrl = null
        this.activeCacheController = null
        this.activeCacheTarget = null
        this.activateCachedSource(this.activeObjectUrl)
        playerLog("buffer", "current Blob ready", { trackId, profile: profileKey })
        this.reportFullyBuffered()
      })
      // Native media playback remains the fallback. A later play attempt may
      // retry the explicit cache without turning the player into error.
      .catch((error: Error) => {
        if (error.name !== "AbortError") {
          playerLog("buffer", "force-cache failed", {
            trackId,
            profile: profileKey,
            message: error.message,
          })
        }
      })
      .finally(() => {
        if (this.activeCacheController === controller) this.activeCacheController = null
        if (this.activeCacheTarget === target) this.activeCacheTarget = null
      })
  }

  private cancelActiveCache() {
    this.activeCacheController?.abort()
    this.activeCacheController = null
    this.activeCacheTarget = null
  }

  private async fetchAudioBlob(url: string, signal: AbortSignal): Promise<Blob> {
    const response = await fetch(url, {
      credentials: "same-origin",
      signal,
    })
    if (!response.ok) throw new Error(`Active audio cache failed: HTTP ${response.status}`)
    return response.blob()
  }

  private activateCachedSource(objectUrl: string) {
    const prev = this.el
    const position = prev.currentTime
    const shouldResume = !prev.paused
    const next = this.createElement()
    next.volume = prev.volume
    next.muted = prev.muted
    next.src = objectUrl

    // Make stale element events inert before unloading it. The replacement is
    // local, so metadata/seek do not require another network request.
    this.el = next
    prev.pause()
    prev.src = ""
    prev.load()

    const resume = () => {
      next.removeEventListener("loadedmetadata", resume)
      if (next !== this.el) return
      next.currentTime = Math.min(position, next.duration || position)
      if (shouldResume) {
        void next.play().catch((error: Error) => {
          this.callbacks?.onPlaybackStateChange("error")
          this.callbacks?.onError(error.message)
        })
      }
    }
    next.addEventListener("loadedmetadata", resume)
    next.load()
  }
}

// Singleton — created once at module load, lives outside React tree
export const audioEngine = new AudioEngine()
