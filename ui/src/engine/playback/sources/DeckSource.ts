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
  load(source: TrackSource, signal: AbortSignal): Promise<SourceMetadata>
  play(when?: number): Promise<void>
  pause(when?: number): void
  seek(seconds: number): Promise<void>
  setRate(ratio: number, when?: number): void
  setLoop(loop: LoopState): void
  getClockAnchor(): PlayheadAnchor
  getBufferedRanges(): BufferedRange[]
  release(): Promise<void>
}

export type DeckSourceFactory = () => DeckSource
