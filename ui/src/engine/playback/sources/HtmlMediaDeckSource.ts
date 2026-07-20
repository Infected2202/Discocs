import type { BufferedRange, LoopState, PlayheadAnchor, SourceMetadata, TrackSource } from "../types"
import type { DeckSource } from "./DeckSource"

export class HtmlMediaDeckSource implements DeckSource {
  readonly kind = "media-element" as const
  readonly output: MediaElementAudioSourceNode

  private readonly context: AudioContext
  private readonly element: HTMLAudioElement
  private ownedObjectUrl: string | null = null
  private released = false

  constructor(context: AudioContext) {
    this.context = context
    this.element = new Audio()
    this.element.preload = "auto"
    this.output = context.createMediaElementSource(this.element)
  }

  async load(source: TrackSource, signal: AbortSignal): Promise<SourceMetadata> {
    this.assertUsable()
    let url = source.url
    if (source.fetchAsBlob) {
      const response = await fetch(source.url, { credentials: "same-origin", signal })
      if (!response.ok) throw new Error(`Audio fetch failed: HTTP ${response.status}`)
      const blob = await response.blob()
      if (signal.aborted) throw new DOMException("Audio load aborted", "AbortError")
      url = URL.createObjectURL(blob)
      this.ownedObjectUrl = url
    }
    if (signal.aborted) throw new DOMException("Audio load aborted", "AbortError")
    this.element.src = url
    this.element.load()
    return {
      duration: Number.isFinite(this.element.duration) ? this.element.duration : null,
      objectUrl: this.ownedObjectUrl !== null,
    }
  }

  async play(when?: number): Promise<void> {
    this.assertUsable()
    if (when !== undefined && when > this.context.currentTime) {
      throw new Error("HTML media sources cannot start sample-accurately in the future")
    }
    await this.element.play()
  }

  pause(when?: number): void {
    this.assertUsable()
    if (when !== undefined && when > this.context.currentTime) {
      throw new Error("HTML media sources cannot pause sample-accurately in the future")
    }
    this.element.pause()
  }

  async seek(seconds: number): Promise<void> {
    this.assertUsable()
    this.element.currentTime = Math.max(0, seconds)
  }

  setRate(ratio: number, when?: number): void {
    this.assertUsable()
    if (when !== undefined && when > this.context.currentTime) {
      throw new Error("HTML media rate changes cannot be scheduled in the future")
    }
    this.element.playbackRate = Math.max(0.25, Math.min(4, ratio))
    this.element.preservesPitch = true
  }

  setLoop(loop: LoopState): void {
    this.assertUsable()
    this.element.loop = loop.enabled && loop.startSeconds === 0
  }

  getClockAnchor(): PlayheadAnchor {
    return {
      mediaSeconds: this.element.currentTime,
      audioTime: this.context.currentTime,
      rate: this.element.playbackRate,
    }
  }

  getBufferedRanges(): BufferedRange[] {
    const ranges: BufferedRange[] = []
    for (let index = 0; index < this.element.buffered.length; index += 1) {
      ranges.push({ start: this.element.buffered.start(index), end: this.element.buffered.end(index) })
    }
    return ranges
  }

  async release(): Promise<void> {
    if (this.released) return
    this.released = true
    this.output.disconnect()
    this.element.pause()
    this.element.src = ""
    this.element.load()
    if (this.ownedObjectUrl) URL.revokeObjectURL(this.ownedObjectUrl)
    this.ownedObjectUrl = null
  }

  private assertUsable(): void {
    if (this.released) throw new Error("Deck source has been released")
  }
}
