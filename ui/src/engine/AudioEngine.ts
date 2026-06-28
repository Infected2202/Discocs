import type { TrackSummary } from "@/api/types"

export type PlaybackState = "idle" | "loading" | "playing" | "paused" | "error"

interface AudioEngineCallbacks {
  onTimeUpdate(currentTime: number, duration: number): void
  onPlaybackStateChange(state: PlaybackState): void
  onEnded(): void
  onError(message: string): void
}

class AudioEngine {
  private readonly el: HTMLAudioElement
  private callbacks: AudioEngineCallbacks | null = null

  constructor() {
    this.el = new Audio()
    this.el.preload = "none"
    this.attachListeners()
  }

  init(callbacks: AudioEngineCallbacks) {
    this.callbacks = callbacks
  }

  load(url: string) {
    this.el.pause()
    this.el.removeAttribute("src")
    this.el.load()
    this.el.src = url
    this.el.load()
  }

  async play(): Promise<void> {
    await this.el.play()
  }

  pause() {
    this.el.pause()
  }

  seek(fraction: number) {
    if (!Number.isFinite(this.el.duration)) return
    this.el.currentTime = fraction * this.el.duration
  }

  seekToSeconds(seconds: number) {
    this.el.currentTime = seconds
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
      artwork: artworkUrl ? [{ src: artworkUrl, sizes: "512x512", type: "image/jpeg" }] : [],
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

  private attachListeners() {
    this.el.addEventListener("timeupdate", () => {
      this.callbacks?.onTimeUpdate(this.el.currentTime, this.el.duration || 0)
    })

    this.el.addEventListener("play", () => {
      this.callbacks?.onPlaybackStateChange("playing")
    })

    // "playing" fires after buffering resumes — fixes spinner stuck after "waiting"
    this.el.addEventListener("playing", () => {
      this.callbacks?.onPlaybackStateChange("playing")
    })

    this.el.addEventListener("pause", () => {
      if (!this.el.ended) this.callbacks?.onPlaybackStateChange("paused")
    })

    this.el.addEventListener("waiting", () => {
      this.callbacks?.onPlaybackStateChange("loading")
    })

    this.el.addEventListener("canplay", () => {
      // only emit if we were loading — play/pause events handle the rest
    })

    this.el.addEventListener("ended", () => {
      this.callbacks?.onEnded()
    })

    this.el.addEventListener("error", () => {
      const err = this.el.error
      const msg = err ? `Media error ${err.code}: ${err.message}` : "Unknown audio error"
      this.callbacks?.onPlaybackStateChange("error")
      this.callbacks?.onError(msg)
    })
  }
}

// Singleton — created once at module load, lives outside React tree
export const audioEngine = new AudioEngine()
