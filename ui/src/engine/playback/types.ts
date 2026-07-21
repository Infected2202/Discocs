export type DeckId = "A" | "B"
export type CommandOrigin = "manual" | "automation" | "system"
export type DeckRole = "program" | "incoming" | "outgoing" | "prepared" | "free"
export type PreparationState = "empty" | "loading" | "ready" | "streaming" | "unavailable"
export type TransportState = "idle" | "loading" | "paused" | "playing" | "ended" | "error"
export type EqBand = "low" | "mid" | "high"

export interface BufferedRange {
  start: number
  end: number
}

export interface PlayheadAnchor {
  mediaSeconds: number
  audioTime: number
  rate: number
}

export interface LoopState {
  enabled: boolean
  startSeconds: number
  endSeconds: number
}

export interface TrackSource {
  url: string
  trackId: number | null
  queueItemId?: string | null
  fetchAsBlob?: boolean
  blob?: Blob
}

export interface SourceMetadata {
  duration: number | null
  objectUrl: boolean
}

export interface CommandMeta {
  origin: CommandOrigin
  transitionId?: string
  clientCommandId?: string
}

export interface DeckSnapshot {
  id: DeckId
  role: DeckRole
  preparation: PreparationState
  transport: TransportState
  trackId: number | null
  queueItemId: string | null
  duration: number | null
  anchor: PlayheadAnchor | null
  buffered: BufferedRange[]
  sourceKind: "media-element" | "signalsmith" | null
  tempoMode: "native" | "pitch-preserving" | "unavailable"
  tempoRatio: number
  degradedReason: string | null
}

export type TempoMaster = DeckId | "clock"
export type DeckSyncPhase = "off" | "pending" | "aligned" | "unavailable"

export interface BeatSyncSnapshot {
  auto: boolean
  master: TempoMaster
  clockBpm: number
  decks: Record<DeckId, {
    enabled: boolean
    phase: DeckSyncPhase
    reason: string | null
  }>
}

export interface DeckSourceUpgradeResult {
  readonly upgraded: boolean
  readonly kind: "media-element" | "signalsmith"
  readonly reason: string | null
}

export interface MixerSnapshot {
  crossfader: number
  masterGain: number
  channelFaders: Record<DeckId, number>
  trims: Record<DeckId, number>
  eq: Record<DeckId, Record<EqBand, number>>
  filters: Record<DeckId, number>
  meters: Record<DeckId | "master", number>
}

export interface HandoverRequest {
  incomingDeck: DeckId
  clientHandoverId: string
}

export interface HandoverResult {
  outgoingDeck: DeckId
  programDeck: DeckId
  clientHandoverId: string
}

export interface PlaybackCapabilities {
  webAudio: boolean
  mediaElementSource: boolean
  ordinary: boolean
  manualMix: boolean
  reasons: string[]
}

export interface EngineError {
  code: string
  message: string
  recoverable: boolean
}

export interface AutomationSnapshot {
  owner: "none" | "automation"
}

export interface PlaybackEngineSnapshot {
  revision: number
  contextState: AudioContextState | "uninitialized"
  programDeck: DeckId | null
  decks: Record<DeckId, DeckSnapshot>
  mixer: MixerSnapshot
  beatSync: BeatSyncSnapshot
  automation: AutomationSnapshot
  capabilities: PlaybackCapabilities
  error: EngineError | null
}
