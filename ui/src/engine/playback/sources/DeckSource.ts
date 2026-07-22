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
  /**
   * Every `when` below is an `AudioContext.currentTime`-relative timestamp:
   * the operation must take effect at that sample-accurate audio time, not
   * immediately. An implementation that cannot honor a future `when` (no
   * sample-accurate scheduling primitive available) must throw rather than
   * silently applying the operation early or late.
   */
  play(when?: number, offsetSeconds?: number): Promise<void>
  pause(when?: number): void | Promise<void>
  seek(seconds: number, when?: number): Promise<void>
  setRate(ratio: number, when?: number): void | Promise<void>
  setLoop(loop: LoopState, when?: number): void | Promise<void>
  getClockAnchor(): PlayheadAnchor
  getBufferedRanges(): BufferedRange[]
  getTransportState?(): import("../types").TransportState
  setStateListener?(listener: (() => void) | null): void
  /**
   * A dedicated channel for the periodic Signalsmith worklet clock tick
   * (distinct from `setStateListener`, which fires on every transport/seek/
   * rate change too): SYNC_REWRITE_PLAN.md §2.1 wires this specifically to
   * the follower phase-offset measurement, which must only ever fire from
   * the real ~0.25s tick, not from every unrelated notify.
   */
  setClockTickListener?(listener: (() => void) | null): void
  release(): Promise<void>
}

export type DeckSourceFactory = () => DeckSource
