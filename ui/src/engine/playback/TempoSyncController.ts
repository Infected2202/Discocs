import { initialTempoSyncState, reduceTempoSync } from "./tempoSync"
import type {
  AlignResultFact,
  SyncMode,
  TempoSyncEffect,
  TempoSyncEvent,
  TempoSyncState,
  TempoSyncTransition,
} from "./tempoSync"
import type { DeckId, TempoMaster } from "./types"
import { reportEngineFailure } from "./reportEngineFailure"

const DECKS: readonly DeckId[] = ["A", "B"]

export interface TempoSyncHost {
  alignFollower(
    deck: DeckId,
    master: TempoMaster,
    mode: SyncMode,
    start: boolean,
    positionHint?: number,
  ): Promise<void>
}

/**
 * Thin effect-executing shell around `reduceTempoSync`: dispatches an event,
 * commits the resulting state synchronously, then runs every effect through
 * the host and feeds the outcome back in as an `align-result` fact. Per R1,
 * `align-result` never itself produces effects, so that feedback step is a
 * single non-recursive hop, not a re-entrant `dispatch` call.
 */
export class TempoSyncController {
  private readonly host: TempoSyncHost
  private state: TempoSyncState = initialTempoSyncState()
  private readonly pendingTransportDispatches = new Map<DeckId, Promise<TempoSyncTransition>>()

  constructor(host: TempoSyncHost) {
    this.host = host
  }

  dispatch(event: TempoSyncEvent): Promise<TempoSyncTransition> {
    if (event.type !== "deck-transport") return this.dispatchOnce(event)

    // Keyed by deck alone, not deck+transport: a deck mid-alignFollower (e.g.
    // PlaybackEngine's setRate -> seek -> play sequence for a BeatSync
    // "start=true" arm) fires its general state listener after *each* step,
    // and PlaybackEngine.handleDeckRuntimeChange re-derives the fact's
    // `transport` fresh every time from the DeckSource's own transport field
    // -- which genuinely still reads "paused" until the trailing `play()`
    // resolves, even though this exact "deck-transport playing" dispatch
    // already committed `transports[deck] = true` up front. Keying by
    // deck+transport treated that transient "paused" reading as a *different*
    // key, so it raced in fresh (via dispatchOnce below) instead of waiting,
    // flipping `transports[deck]` back to false for the remainder of the
    // in-flight arm and leaving it stuck there -- silently corrupting state
    // that only a later, unrelated "playing" fact (e.g. the next Signalsmith
    // clock tick's own notify()) would happen to correct, spuriously
    // re-triggering `armFollower` in the process. Keying by deck alone
    // serializes *every* transport reading for a deck behind whichever
    // dispatch for that deck is already in flight, so a stale mid-sequence
    // reading can never independently apply itself.
    const key = event.deck
    const pending = this.pendingTransportDispatches.get(key)
    if (pending) {
      return pending.then(() => reduceTempoSync(this.state, event))
    }

    const operation = this.dispatchOnce(event)
    this.pendingTransportDispatches.set(key, operation)
    const clearPending = () => {
      if (this.pendingTransportDispatches.get(key) === operation) {
        this.pendingTransportDispatches.delete(key)
      }
    }
    void operation.then(clearPending, clearPending)
    return operation
  }

  private async dispatchOnce(event: TempoSyncEvent): Promise<TempoSyncTransition> {
    const transition = reduceTempoSync(this.state, event)
    this.state = transition.state
    await Promise.all(transition.effects.map((effect) => this.executeEffect(effect, event)))
    return transition
  }

  isFollower(deck: DeckId): boolean {
    return this.state.master !== deck && this.state.followers[deck].enabled
  }

  isMaster(target: TempoMaster): boolean {
    return this.state.master === target
  }

  hasEngagedFollowers(): boolean {
    return DECKS.some((deck) => this.isFollower(deck))
  }

  getState(): TempoSyncState {
    return this.state
  }

  reset(): void {
    this.state = initialTempoSyncState()
  }

  private async executeEffect(effect: TempoSyncEffect, sourceEvent: TempoSyncEvent): Promise<void> {
    // A dispatched "now playing" fact is itself the trigger that starts a
    // follower's playback (see SYNC_REWRITE_PLAN.md §2 / PlaybackEngine's
    // playDeck): only the effect produced by that exact fact, for that exact
    // deck, should tell the host to actually call runtime.play(). Every other
    // source of an align-follower effect (master reassignment, drift
    // correction, arming resolving to ready) re-aligns a deck in place.
    const start =
      sourceEvent.type === "deck-transport" &&
      sourceEvent.transport === "playing" &&
      sourceEvent.deck === effect.deck

    let result: AlignResultFact
    try {
      await this.host.alignFollower(effect.deck, effect.master, effect.mode, start)
      result = { type: "align-result", deck: effect.deck, requestId: effect.requestId, outcome: "ok" }
    } catch (error) {
      reportEngineFailure(`TempoSyncController.alignFollower(${effect.deck})`, error)
      result = {
        type: "align-result",
        deck: effect.deck,
        requestId: effect.requestId,
        outcome: "error",
        reason: error instanceof Error ? error.message : "Alignment failed",
      }
    }
    this.state = reduceTempoSync(this.state, result).state
  }
}
