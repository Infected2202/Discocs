import { tempoRatioFor } from "./beatSync"
import type { DeckId, TempoMaster, TransportState } from "./types"

export type SyncMode = "beat" | "tempo"
export type FollowerPhase = "off" | "arming" | "pending" | "aligned" | "unavailable"

export interface DeckCapabilityState {
  readonly hasTrack: boolean
  readonly feasible: boolean
  readonly ready: boolean
  readonly bpm: number | null
}

export interface FollowerState {
  readonly enabled: boolean
  readonly mode: SyncMode
  readonly phase: FollowerPhase
  readonly reason: string | null
  readonly phaseOffsetBeats: number
  readonly lastAlignAt: number | null
  readonly inFlightRequestId: string | null
}

export interface TempoSyncState {
  readonly auto: boolean
  readonly master: TempoMaster
  readonly clockBpm: number
  readonly transports: Record<DeckId, boolean>
  readonly capabilities: Record<DeckId, DeckCapabilityState>
  readonly followers: Record<DeckId, FollowerState>
  readonly nextRequestId: number
}

export interface DeckTransportFact {
  readonly type: "deck-transport"
  readonly deck: DeckId
  readonly transport: TransportState
}

export interface DeckCapabilityFact {
  readonly type: "deck-capability"
  readonly deck: DeckId
  readonly hasTrack: boolean
  readonly feasible: boolean
  readonly ready: boolean
  readonly bpm: number | null
}

export interface PhaseOffsetObservedFact {
  readonly type: "phase-offset-observed"
  readonly deck: DeckId
  readonly offsetBeats: number
  readonly at: number
}

export interface AlignResultFact {
  readonly type: "align-result"
  readonly deck: DeckId
  readonly requestId: string
  readonly outcome: "ok" | "error"
  readonly reason?: string | null
}

export type TempoSyncFact =
  | DeckTransportFact
  | DeckCapabilityFact
  | PhaseOffsetObservedFact
  | AlignResultFact

export interface ToggleSyncCommand {
  readonly type: "toggle-sync"
  readonly deck: DeckId
  readonly mode: SyncMode
}

export interface SetDeckMasterCommand {
  readonly type: "set-deck-master"
  readonly deck: DeckId
}

export interface SetClockMasterCommand {
  readonly type: "set-clock-master"
}

export interface SetAutoMasterCommand {
  readonly type: "set-auto-master"
}

export type TempoSyncCommand =
  | ToggleSyncCommand
  | SetDeckMasterCommand
  | SetClockMasterCommand
  | SetAutoMasterCommand

export type TempoSyncEvent = TempoSyncFact | TempoSyncCommand

export interface AlignFollowerEffect {
  readonly type: "align-follower"
  readonly deck: DeckId
  readonly master: TempoMaster
  readonly mode: SyncMode
  readonly requestId: string
}

export type TempoSyncEffect = AlignFollowerEffect

export interface TempoSyncTransition {
  readonly state: TempoSyncState
  readonly effects: readonly TempoSyncEffect[]
}

const DECKS: readonly DeckId[] = ["A", "B"]

/**
 * Past this many beats of accumulated drift a BeatSync follower is worth
 * re-aligning; below it, sub-beat measurement jitter would cause audible
 * re-seeking for no perceptible benefit. TempoSync never reads this constant
 * at all — phase drift is its whole point, it only ever updates the readout.
 */
export const BEAT_SYNC_DRIFT_THRESHOLD_BEATS = 0.06

/**
 * Minimum spacing between BeatSync auto-corrections for the same deck.
 * Re-aligning on every ~0.25s clock tick once past the drift threshold would
 * fight ordinary measurement noise with audible micro-seeks; this bounds
 * correction frequency without needing hysteresis on the threshold itself.
 */
export const BEAT_SYNC_REALIGN_COOLDOWN_SECONDS = 2

function emptyDeckCapability(): DeckCapabilityState {
  return { hasTrack: false, feasible: false, ready: false, bpm: null }
}

function emptyFollower(): FollowerState {
  return {
    enabled: false,
    mode: "beat",
    phase: "off",
    reason: null,
    phaseOffsetBeats: 0,
    lastAlignAt: null,
    inFlightRequestId: null,
  }
}

export function initialTempoSyncState(): TempoSyncState {
  return {
    auto: true,
    master: "clock",
    clockBpm: 126,
    transports: { A: false, B: false },
    capabilities: { A: emptyDeckCapability(), B: emptyDeckCapability() },
    followers: { A: emptyFollower(), B: emptyFollower() },
    nextRequestId: 1,
  }
}

function isPlaying(state: TempoSyncState, deck: DeckId): boolean {
  return state.transports[deck]
}

function firstPlayingDeck(state: TempoSyncState, exclude?: DeckId): DeckId | null {
  for (const deck of DECKS) {
    if (deck !== exclude && isPlaying(state, deck)) return deck
  }
  return null
}

function masterTempo(state: TempoSyncState): number {
  if (state.master === "clock") return state.clockBpm
  return state.capabilities[state.master].bpm ?? state.clockBpm
}

function setFollower(state: TempoSyncState, deck: DeckId, follower: FollowerState): TempoSyncState {
  return { ...state, followers: { ...state.followers, [deck]: follower } }
}

function allocateRequestId(state: TempoSyncState): { requestId: string; state: TempoSyncState } {
  return { requestId: `align-${state.nextRequestId}`, state: { ...state, nextRequestId: state.nextRequestId + 1 } }
}

function capabilitiesEqual(a: DeckCapabilityState, b: DeckCapabilityState): boolean {
  return a.hasTrack === b.hasTrack && a.feasible === b.feasible && a.ready === b.ready && a.bpm === b.bpm
}

function synchronousRejectionReason(
  state: TempoSyncState,
  deck: DeckId,
  capability: DeckCapabilityState,
): string | null {
  if (!capability.hasTrack) return `Deck ${deck} has no track loaded`
  if (!capability.feasible) return `Deck ${deck} will never produce a beat timeline`
  if (deck !== state.master && capability.ready && capability.bpm !== null) {
    if (tempoRatioFor(masterTempo(state), capability.bpm) === null) {
      return `Master tempo is outside Deck ${deck}'s beat-sync range`
    }
  }
  return null
}

function reconcileMasterFollower(state: TempoSyncState, master: TempoMaster): TempoSyncState {
  if (master === "clock") return state
  const follower = state.followers[master]
  if (!follower.enabled || follower.phase === "arming" || follower.phase === "unavailable") return state
  const phase: FollowerPhase = isPlaying(state, master) ? "aligned" : "pending"
  return setFollower(state, master, { ...follower, phase, reason: null, inFlightRequestId: null })
}

function armFollower(state: TempoSyncState, deck: DeckId, follower: FollowerState): TempoSyncTransition {
  const allocated = allocateRequestId(state)
  const next = setFollower(allocated.state, deck, {
    ...follower,
    phase: "pending",
    reason: null,
    inFlightRequestId: allocated.requestId,
  })
  return {
    state: next,
    effects: [
      { type: "align-follower", deck, master: next.master, mode: follower.mode, requestId: allocated.requestId },
    ],
  }
}

function promoteMaster(state: TempoSyncState, master: TempoMaster): TempoSyncTransition {
  let next: TempoSyncState = { ...state, master }
  next = reconcileMasterFollower(next, master)
  const effects: TempoSyncEffect[] = []
  for (const deck of DECKS) {
    if (deck === master) continue
    const follower = next.followers[deck]
    if (!follower.enabled) continue
    if (follower.phase === "arming" || follower.phase === "unavailable") continue
    if (follower.inFlightRequestId !== null) continue
    const armed = armFollower(next, deck, follower)
    next = armed.state
    effects.push(...armed.effects)
  }
  return { state: next, effects }
}

function handleDeckTransport(state: TempoSyncState, fact: DeckTransportFact): TempoSyncTransition {
  const nowPlaying = fact.transport === "playing"
  if (state.transports[fact.deck] === nowPlaying) return { state, effects: [] }

  let next: TempoSyncState = { ...state, transports: { ...state.transports, [fact.deck]: nowPlaying } }
  let effects: TempoSyncEffect[] = []

  if (next.auto) {
    if (nowPlaying && next.master === "clock") {
      const promoted = promoteMaster(next, fact.deck)
      next = promoted.state
      effects = effects.concat(promoted.effects)
    } else if (!nowPlaying && next.master === fact.deck) {
      const fallback = firstPlayingDeck(next, fact.deck) ?? "clock"
      const promoted = promoteMaster(next, fallback)
      next = promoted.state
      effects = effects.concat(promoted.effects)
    }
  }

  if (fact.deck === next.master) {
    next = reconcileMasterFollower(next, next.master)
    return { state: next, effects }
  }

  const follower = next.followers[fact.deck]
  if (
    nowPlaying &&
    follower.enabled &&
    (follower.phase === "pending" || follower.phase === "aligned") &&
    follower.inFlightRequestId === null
  ) {
    const armed = armFollower(next, fact.deck, follower)
    next = armed.state
    effects = effects.concat(armed.effects)
  }

  return { state: next, effects }
}

function handleDeckCapability(state: TempoSyncState, fact: DeckCapabilityFact): TempoSyncTransition {
  const next: DeckCapabilityState = { hasTrack: fact.hasTrack, feasible: fact.feasible, ready: fact.ready, bpm: fact.bpm }
  if (capabilitiesEqual(state.capabilities[fact.deck], next)) return { state, effects: [] }

  let nextState: TempoSyncState = { ...state, capabilities: { ...state.capabilities, [fact.deck]: next } }
  const follower = nextState.followers[fact.deck]
  if (follower.phase !== "arming") return { state: nextState, effects: [] }

  if (next.ready) {
    const reason = synchronousRejectionReason(nextState, fact.deck, next)
    if (reason !== null) {
      nextState = setFollower(nextState, fact.deck, {
        ...follower,
        enabled: false,
        phase: "unavailable",
        reason,
        inFlightRequestId: null,
      })
      return { state: nextState, effects: [] }
    }
    if (fact.deck === nextState.master) {
      nextState = reconcileMasterFollower(nextState, nextState.master)
      return { state: nextState, effects: [] }
    }
    return armFollower(nextState, fact.deck, follower)
  }

  if (!next.feasible) {
    nextState = setFollower(nextState, fact.deck, {
      ...follower,
      enabled: false,
      phase: "unavailable",
      reason: `Deck ${fact.deck} will never produce a beat timeline`,
      inFlightRequestId: null,
    })
    return { state: nextState, effects: [] }
  }

  return { state: nextState, effects: [] }
}

function handlePhaseOffsetObserved(state: TempoSyncState, fact: PhaseOffsetObservedFact): TempoSyncTransition {
  const follower = state.followers[fact.deck]
  if (!follower.enabled || fact.deck === state.master) return { state, effects: [] }

  const next = setFollower(state, fact.deck, { ...follower, phaseOffsetBeats: fact.offsetBeats })
  if (follower.mode === "tempo") return { state: next, effects: [] }

  if ((follower.phase !== "pending" && follower.phase !== "aligned") || follower.inFlightRequestId !== null) {
    return { state: next, effects: [] }
  }
  if (Math.abs(fact.offsetBeats) < BEAT_SYNC_DRIFT_THRESHOLD_BEATS) return { state: next, effects: [] }
  if (follower.lastAlignAt !== null && fact.at - follower.lastAlignAt < BEAT_SYNC_REALIGN_COOLDOWN_SECONDS) {
    return { state: next, effects: [] }
  }

  const updated = next.followers[fact.deck]
  return armFollower(next, fact.deck, { ...updated, lastAlignAt: fact.at })
}

function handleAlignResult(state: TempoSyncState, fact: AlignResultFact): TempoSyncTransition {
  const follower = state.followers[fact.deck]
  if (follower.inFlightRequestId !== fact.requestId) return { state, effects: [] }
  const phase: FollowerPhase = fact.outcome === "ok" ? "aligned" : "unavailable"
  const reason = fact.outcome === "ok" ? null : fact.reason ?? "Alignment failed"
  const next = setFollower(state, fact.deck, { ...follower, phase, reason, inFlightRequestId: null })
  return { state: next, effects: [] }
}

function handleToggleSync(state: TempoSyncState, deck: DeckId, mode: SyncMode): TempoSyncTransition {
  const follower = state.followers[deck]

  if (follower.enabled && follower.mode === mode) {
    return { state: setFollower(state, deck, emptyFollower()), effects: [] }
  }
  if (follower.enabled) {
    return { state: setFollower(state, deck, { ...follower, mode }), effects: [] }
  }

  const capability = state.capabilities[deck]
  const reason = synchronousRejectionReason(state, deck, capability)
  if (reason !== null) {
    return { state: setFollower(state, deck, { ...emptyFollower(), mode, phase: "unavailable", reason }), effects: [] }
  }

  if (deck === state.master) {
    const phase: FollowerPhase = isPlaying(state, deck) ? "aligned" : "pending"
    return { state: setFollower(state, deck, { ...emptyFollower(), enabled: true, mode, phase }), effects: [] }
  }

  if (!capability.ready) {
    return { state: setFollower(state, deck, { ...emptyFollower(), enabled: true, mode, phase: "arming" }), effects: [] }
  }

  return armFollower(state, deck, { ...emptyFollower(), enabled: true, mode })
}

function handleSetDeckMaster(state: TempoSyncState, deck: DeckId): TempoSyncTransition {
  if (!state.transports[deck]) return { state, effects: [] }
  if (!state.auto && state.master === deck) return { state, effects: [] }
  return promoteMaster({ ...state, auto: false }, deck)
}

function handleSetClockMaster(state: TempoSyncState): TempoSyncTransition {
  if (firstPlayingDeck(state) !== null) return { state, effects: [] }
  if (!state.auto && state.master === "clock") return { state, effects: [] }
  return promoteMaster({ ...state, auto: false }, "clock")
}

function handleSetAutoMaster(state: TempoSyncState): TempoSyncTransition {
  if (state.auto) return { state, effects: [] }
  const next = { ...state, auto: true }
  const desired = firstPlayingDeck(next) ?? "clock"
  if (desired === next.master) return { state: next, effects: [] }
  return promoteMaster(next, desired)
}

function assertNever(event: never): never {
  throw new Error(`Unhandled tempo sync event: ${JSON.stringify(event)}`)
}

export function reduceTempoSync(state: TempoSyncState, event: TempoSyncEvent): TempoSyncTransition {
  switch (event.type) {
    case "deck-transport":
      return handleDeckTransport(state, event)
    case "deck-capability":
      return handleDeckCapability(state, event)
    case "phase-offset-observed":
      return handlePhaseOffsetObserved(state, event)
    case "align-result":
      return handleAlignResult(state, event)
    case "toggle-sync":
      return handleToggleSync(state, event.deck, event.mode)
    case "set-deck-master":
      return handleSetDeckMaster(state, event.deck)
    case "set-clock-master":
      return handleSetClockMaster(state)
    case "set-auto-master":
      return handleSetAutoMaster(state)
    default:
      return assertNever(event)
  }
}

export function canEngageBeatSync(state: TempoSyncState, deck: DeckId): boolean {
  const capability = state.capabilities[deck]
  return capability.hasTrack && capability.feasible
}

export function canEngageTempoSync(state: TempoSyncState, deck: DeckId): boolean {
  return canEngageBeatSync(state, deck)
}

export function canBecomeMaster(state: TempoSyncState, deck: DeckId): boolean {
  return state.transports[deck] && state.master !== deck
}

export function canBecomeClockMaster(state: TempoSyncState): boolean {
  return firstPlayingDeck(state) === null && state.master !== "clock"
}
