import type {
  BufferedRange,
  LoopState,
  PlayheadAnchor,
  SourceMetadata,
  TrackSource,
} from "../types"

export interface DeckSource {
  readonly kind: "media-element" | "signalsmith"
  readonly output: AudioNode
  readonly activationDelaySeconds?: number
  load(source: TrackSource, signal: AbortSignal): Promise<SourceMetadata>
  play(when?: number): Promise<void>
  pause(when?: number): void | Promise<void>
  seek(seconds: number): Promise<void>
  setRate(ratio: number, when?: number): void | Promise<void>
  setLoop(loop: LoopState): void | Promise<void>
  getClockAnchor(): PlayheadAnchor
  getBufferedRanges(): BufferedRange[]
  getTransportState?(): import("../types").TransportState
  setStateListener?(listener: (() => void) | null): void
  release(): Promise<void>
}

export type DeckSourceFactory = () => DeckSource
