import type { TrackSummary } from "@/api/types"

export type PlaybackState = "idle" | "loading" | "playing" | "paused" | "error"

interface AudioEngineCallbacks {
  onTimeUpdate(currentTime: number, duration: number): void
  onPlaybackStateChange(state: PlaybackState): void
  onBufferUpdate(fraction: number): void
  onEnded(): void
  onError(message: string): void
}

class AudioEngine {
  private el: HTMLAudioElement
  private callbacks: AudioEngineCallbacks | null = null

  constructor() {
    this.el = this.createElement()
  }

  init(callbacks: AudioEngineCallbacks) {
    this.callbacks = callbacks
  }

  load(url: string) {
    // Явно освобождаем буфер старого элемента — src='' надёжнее removeAttribute
    const prev = this.el
    prev.pause()
    prev.src = ""
    prev.load()

    // Новый элемент — Chrome гарантированно освобождает нативный PCM буфер
    // когда старый элемент теряет все ссылки и GC его собирает
    this.el = this.createElement()
    this.el.volume = prev.volume
    this.el.muted = prev.muted
    this.el.src = url
    this.el.load()

    // Reset immediately — otherwise the buffered indicator briefly shows
    // the previous track's fully-downloaded range on the new one.
    this.callbacks?.onBufferUpdate(0)
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

    this.el = this.createElement()
    this.el.volume = volume
    this.el.muted = muted

    this.callbacks?.onTimeUpdate(0, 0)
    this.callbacks?.onBufferUpdate(0)
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
   * load(): with preload="none" duration is unknown until playback starts,
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
    el.preload = "none"
    this.attachListeners(el)
    return el
  }

  private attachListeners(el: HTMLAudioElement) {
    el.addEventListener("timeupdate", () => {
      this.callbacks?.onTimeUpdate(el.currentTime, el.duration || 0)
    })

    const reportBuffered = () => {
      if (!Number.isFinite(el.duration) || el.duration <= 0) return
      // `buffered` can have gaps after a seek — use the range that covers
      // (or is closest ahead of) currentTime, not just the last one.
      const { buffered, currentTime } = el
      let end = 0
      for (let i = 0; i < buffered.length; i++) {
        if (buffered.start(i) <= currentTime && buffered.end(i) > end) {
          end = buffered.end(i)
        }
      }
      this.callbacks?.onBufferUpdate(Math.min(1, end / el.duration))
    }

    el.addEventListener("progress", reportBuffered)
    el.addEventListener("loadedmetadata", reportBuffered)
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
}

// Singleton — created once at module load, lives outside React tree
export const audioEngine = new AudioEngine()
