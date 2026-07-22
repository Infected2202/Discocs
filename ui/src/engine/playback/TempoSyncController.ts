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

  constructor(host: TempoSyncHost) {
    this.host = host
  }

  async dispatch(event: TempoSyncEvent): Promise<TempoSyncTransition> {
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
