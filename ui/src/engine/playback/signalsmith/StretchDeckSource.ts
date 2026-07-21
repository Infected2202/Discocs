import type {
  BufferedRange,
  LoopState,
  PlayheadAnchor,
  SourceMetadata,
  TrackSource,
  TransportState,
} from "../types"
import type { DeckSource } from "../sources/DeckSource"
import { createSignalsmithNode } from "./assets"
import { detectStretchCapability } from "./capabilities"
import { StretchAdapter } from "./StretchAdapter"
import type { StretchNode } from "./types"

type NodeFactory = (context: AudioContext, channels: number) => Promise<StretchNode>

export interface StretchDeckSourceDependencies {
  readonly createNode?: NodeFactory
}

export class StretchDeckSource implements DeckSource {
  readonly kind = "signalsmith" as const
  readonly output: GainNode
  private readonly context: AudioContext
  private readonly createNode: NodeFactory
  private adapter: StretchAdapter | null = null
  private compressedBlob: Blob | null = null
  private duration: number | null = null
  private transport: TransportState = "idle"
  private loop: LoopState = { enabled: false, startSeconds: 0, endSeconds: 0 }
  private stateListener: (() => void) | null = null
  private released = false

  constructor(context: AudioContext, dependencies: StretchDeckSourceDependencies = {}) {
    this.context = context
    this.output = context.createGain()
    this.createNode = dependencies.createNode ?? ((audioContext, channels) => createSignalsmithNode(audioContext, {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [channels],
    }))
  }

  get activationDelaySeconds(): number {
    return this.adapter?.schedulingLeadSeconds ?? 0
  }

  async load(source: TrackSource, signal: AbortSignal): Promise<SourceMetadata> {
    this.assertUsable()
    const capabilityFailure = detectStretchCapability()
    if (capabilityFailure) throw new Error(capabilityFailure.message)
    this.transport = "loading"
    this.notify()
    try {
      const blob = source.blob ?? await this.fetchBlob(source.url, signal)
      this.compressedBlob = blob
      const encoded = await this.compressedBlob.arrayBuffer()
      if (signal.aborted) throw new DOMException("Audio load aborted", "AbortError")
      const decoded = await this.context.decodeAudioData(encoded)
      if (signal.aborted) throw new DOMException("Audio load aborted", "AbortError")
      if (decoded.numberOfChannels < 1 || decoded.length < 1) throw new Error("Decoded track contains no audio")

      const node = await this.createNode(this.context, decoded.numberOfChannels)
      if (signal.aborted) {
        node.disconnect()
        node.port?.close()
        throw new DOMException("Audio load aborted", "AbortError")
      }
      const adapter = new StretchAdapter(this.context, node)
      this.adapter = adapter
      await adapter.initialize("default")
      adapter.output.connect(this.output)
      const channels = Array.from(
        { length: decoded.numberOfChannels },
        (_, channel) => Float32Array.from(decoded.getChannelData(channel)),
      )
      await adapter.append(channels)
      this.duration = decoded.duration
      await node.setUpdateInterval(0.25, (inputTime) => this.handleClockUpdate(inputTime))
      this.transport = "paused"
      this.notify()
      return { duration: decoded.duration, objectUrl: false }
    } catch (error) {
      this.transport = "error"
      await this.releaseAdapter()
      throw error
    }
  }

  async play(when?: number, offsetSeconds?: number): Promise<void> {
    this.assertUsable()
    await this.requireAdapter().start(offsetSeconds, when)
    this.transport = "playing"
    this.notify()
  }

  async pause(when?: number): Promise<void> {
    this.assertUsable()
    await this.requireAdapter().stop(when)
    this.transport = "paused"
    this.notify()
  }

  async seek(seconds: number, when?: number): Promise<void> {
    this.assertUsable()
    const target = Math.max(0, Math.min(seconds, this.duration ?? Number.POSITIVE_INFINITY))
    await this.requireAdapter().seek(target, when)
    this.notify()
  }

  async setRate(ratio: number, when?: number): Promise<void> {
    this.assertUsable()
    await this.requireAdapter().setRate(Math.max(0.5, Math.min(2, ratio)), when)
    this.notify()
  }

  async setLoop(loop: LoopState, when?: number): Promise<void> {
    this.assertUsable()
    if (loop.enabled && (loop.startSeconds < 0 || loop.endSeconds <= loop.startSeconds)) {
      throw new RangeError("Loop end must be after its non-negative start")
    }
    this.loop = loop
    await this.requireAdapter().setLoop(loop, when)
    this.notify()
  }

  getClockAnchor(): PlayheadAnchor {
    return this.requireAdapter().getClockAnchor()
  }

  getBufferedRanges(): BufferedRange[] {
    return this.duration === null ? [] : [{ start: 0, end: this.duration }]
  }

  getTransportState(): TransportState {
    return this.transport
  }

  setStateListener(listener: (() => void) | null): void {
    this.stateListener = listener
  }

  async release(): Promise<void> {
    if (this.released) return
    this.released = true
    this.transport = "idle"
    await this.releaseAdapter()
    this.compressedBlob = null
    this.output.disconnect()
    this.stateListener = null
  }

  private async fetchBlob(url: string, signal: AbortSignal): Promise<Blob> {
    const response = await fetch(url, { credentials: "same-origin", signal })
    if (!response.ok) throw new Error(`Audio fetch failed: HTTP ${response.status}`)
    return response.blob()
  }

  private handleClockUpdate(inputTime: number): void {
    if (this.released) return
    if (
      this.transport !== "ended"
      && this.duration !== null
      && inputTime >= this.duration - 0.05
      && !this.loop.enabled
    ) {
      this.transport = "ended"
      void this.adapter?.stop()
    }
    this.notify()
  }

  private async releaseAdapter(): Promise<void> {
    const adapter = this.adapter
    this.adapter = null
    await adapter?.release()
  }

  private requireAdapter(): StretchAdapter {
    if (!this.adapter) throw new Error("Signalsmith deck is not loaded")
    return this.adapter
  }

  private assertUsable(): void {
    if (this.released) throw new Error("Deck source has been released")
  }

  private notify(): void {
    this.stateListener?.()
  }
}
