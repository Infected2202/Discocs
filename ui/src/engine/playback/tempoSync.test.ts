import { describe, expect, it } from "vitest"
import {
  BEAT_SYNC_DRIFT_THRESHOLD_BEATS,
  BEAT_SYNC_REALIGN_COOLDOWN_SECONDS,
  canBecomeClockMaster,
  canBecomeMaster,
  canEngageBeatSync,
  canEngageTempoSync,
  initialTempoSyncState,
  reduceTempoSync,
  type DeckId,
  type TempoSyncEffect,
  type TempoSyncEvent,
  type TempoSyncState,
} from "./tempoSync"

function apply(state: TempoSyncState, events: TempoSyncEvent[]): { state: TempoSyncState; effects: TempoSyncEffect[] } {
  let effects: TempoSyncEffect[] = []
  for (const event of events) {
    const result = reduceTempoSync(state, event)
    state = result.state
    effects = effects.concat(result.effects)
  }
  return { state, effects }
}

function playing(deck: DeckId): TempoSyncEvent {
  return { type: "deck-transport", deck, transport: "playing" }
}

function paused(deck: DeckId): TempoSyncEvent {
  return { type: "deck-transport", deck, transport: "paused" }
}

function ready(deck: DeckId, bpm: number): TempoSyncEvent {
  return { type: "deck-capability", deck, hasTrack: true, feasible: true, ready: true, bpm }
}

function arming(deck: DeckId): TempoSyncEvent {
  return { type: "deck-capability", deck, hasTrack: true, feasible: true, ready: false, bpm: null }
}

describe("tempo sync reducer: AUTO promotion and handoff", () => {
  it("promotes the first playing deck to master", () => {
    const initial = initialTempoSyncState()
    expect(initial.master).toBe("clock")

    const { state } = apply(initial, [playing("A")])
    expect(state.master).toBe("A")
    expect(state.auto).toBe(true)
  })

  it("does not steal master from an already-playing deck when a second deck starts", () => {
    const { state } = apply(initialTempoSyncState(), [playing("A"), playing("B")])
    expect(state.master).toBe("A")
  })

  it("hands off to another playing deck when the master stops", () => {
    const { state } = apply(initialTempoSyncState(), [playing("A"), playing("B"), paused("A")])
    expect(state.master).toBe("B")
  })

  it("reverts to the clock when the master stops and nothing else is playing", () => {
    const { state } = apply(initialTempoSyncState(), [playing("A"), paused("A")])
    expect(state.master).toBe("clock")
    expect(state.auto).toBe(true)
  })
})

describe("tempo sync reducer: CLOCK gating", () => {
  it("rejects set-clock-master while any deck is playing", () => {
    const { state } = apply(initialTempoSyncState(), [playing("A")])
    expect(canBecomeClockMaster(state)).toBe(false)

    const result = reduceTempoSync(state, { type: "set-clock-master" })
    expect(result.state).toBe(state)
    expect(result.state.master).toBe("A")
  })

  it("allows pinning the clock as master once nothing is playing", () => {
    const { state: overridden } = apply(initialTempoSyncState(), [
      playing("A"),
      { type: "set-deck-master", deck: "A" },
      paused("A"),
    ])
    expect(overridden.master).toBe("A")
    expect(overridden.auto).toBe(false)
    expect(canBecomeClockMaster(overridden)).toBe(true)

    const result = reduceTempoSync(overridden, { type: "set-clock-master" })
    expect(result.state.master).toBe("clock")
    expect(result.state.auto).toBe(false)
    expect(canBecomeClockMaster(result.state)).toBe(false)
  })
})

describe("tempo sync reducer: MASTER override", () => {
  it("lets an explicit deck master override AUTO and persists through that deck stopping", () => {
    const { state: afterPlaying } = apply(initialTempoSyncState(), [playing("A"), playing("B")])
    expect(afterPlaying.master).toBe("A")
    expect(canBecomeMaster(afterPlaying, "B")).toBe(true)

    const overridden = reduceTempoSync(afterPlaying, { type: "set-deck-master", deck: "B" })
    expect(overridden.state.master).toBe("B")
    expect(overridden.state.auto).toBe(false)

    const afterBStops = reduceTempoSync(overridden.state, paused("B"))
    expect(afterBStops.state.master).toBe("B")
    expect(afterBStops.state.auto).toBe(false)

    const afterAStops = reduceTempoSync(afterBStops.state, paused("A"))
    expect(afterAStops.state.master).toBe("B")
  })

  it("re-enabling AUTO recomputes master from current transport instead of staying pinned", () => {
    const { state: overridden } = apply(initialTempoSyncState(), [
      playing("A"),
      playing("B"),
      { type: "set-deck-master", deck: "B" },
      paused("B"),
      paused("A"),
    ])
    expect(overridden.master).toBe("B")

    const reEnabled = reduceTempoSync(overridden, { type: "set-auto-master" })
    expect(reEnabled.state.auto).toBe(true)
    expect(reEnabled.state.master).toBe("clock")
  })

  it("rejects set-deck-master for a deck that is not playing", () => {
    const initial = initialTempoSyncState()
    const result = reduceTempoSync(initial, { type: "set-deck-master", deck: "A" })
    expect(result.state).toBe(initial)
    expect(canBecomeMaster(initial, "A")).toBe(false)
  })

  it("moves an engaged ex-master's own SYNC selection onto the new master as a follower", () => {
    const { state } = apply(initialTempoSyncState(), [
      playing("A"),
      ready("A", 120),
      { type: "toggle-sync", deck: "A", mode: "beat" },
      playing("B"),
      paused("A"),
    ])
    expect(state.master).toBe("B")
    expect(state.followers.A.enabled).toBe(true)
    expect(state.followers.A.phase).toBe("pending")
    expect(state.followers.A.inFlightRequestId).not.toBeNull()
  })
})

describe("tempo sync reducer: synchronous SYNC rejection", () => {
  it("never sets enabled true for a deck with no track loaded", () => {
    const initial = initialTempoSyncState()
    expect(canEngageBeatSync(initial, "B")).toBe(false)

    const result = reduceTempoSync(initial, { type: "toggle-sync", deck: "B", mode: "beat" })
    expect(result.state.followers.B.enabled).toBe(false)
    expect(result.state.followers.B.phase).toBe("unavailable")
    expect(result.state.followers.B.reason).toMatch(/no track/i)
  })

  it("never sets enabled true for a track that will never produce a beat timeline", () => {
    const initial = initialTempoSyncState()
    const withCapability = reduceTempoSync(initial, {
      type: "deck-capability",
      deck: "B",
      hasTrack: true,
      feasible: false,
      ready: false,
      bpm: null,
    }).state
    expect(canEngageTempoSync(withCapability, "B")).toBe(false)

    const result = reduceTempoSync(withCapability, { type: "toggle-sync", deck: "B", mode: "tempo" })
    expect(result.state.followers.B.enabled).toBe(false)
    expect(result.state.followers.B.phase).toBe("unavailable")
  })

  it("rejects a feasible deck whose bpm cannot reach the master within the tempo range", () => {
    const { state } = apply(initialTempoSyncState(), [ready("B", 60)])
    const result = reduceTempoSync(state, { type: "toggle-sync", deck: "B", mode: "beat" })
    expect(result.state.followers.B.enabled).toBe(false)
    expect(result.state.followers.B.phase).toBe("unavailable")
  })
})

describe("tempo sync reducer: arming resolves on a later capability fact", () => {
  it("engages as arming with no effect, then transitions and aligns once ready", () => {
    const { state: afterMaster } = apply(initialTempoSyncState(), [playing("A"), ready("A", 128), arming("B")])

    const armedResult = reduceTempoSync(afterMaster, { type: "toggle-sync", deck: "B", mode: "beat" })
    expect(armedResult.effects).toHaveLength(0)
    expect(armedResult.state.followers.B.enabled).toBe(true)
    expect(armedResult.state.followers.B.phase).toBe("arming")

    const resolved = reduceTempoSync(armedResult.state, ready("B", 128))
    expect(resolved.effects).toHaveLength(1)
    expect(resolved.effects[0]).toMatchObject({ type: "align-follower", deck: "B", master: "A", mode: "beat" })
    expect(resolved.state.followers.B.phase).toBe("pending")
    expect(resolved.state.followers.B.inFlightRequestId).toBe(resolved.effects[0].requestId)
  })

  it("does not emit an effect while still arming", () => {
    const armedResult = reduceTempoSync(initialTempoSyncState(), arming("A"))
    const stillArming = reduceTempoSync(
      reduceTempoSync(armedResult.state, { type: "toggle-sync", deck: "A", mode: "beat" }).state,
      arming("A"),
    )
    expect(stillArming.effects).toHaveLength(0)
    expect(stillArming.state.followers.A.phase).toBe("arming")
  })

  it("resolves arming to unavailable when the deck turns out to never produce a beat timeline", () => {
    const armed = reduceTempoSync(
      reduceTempoSync(initialTempoSyncState(), arming("B")).state,
      { type: "toggle-sync", deck: "B", mode: "beat" },
    )
    expect(armed.state.followers.B.phase).toBe("arming")

    const resolved = reduceTempoSync(armed.state, {
      type: "deck-capability",
      deck: "B",
      hasTrack: true,
      feasible: false,
      ready: false,
      bpm: null,
    })
    expect(resolved.effects).toHaveLength(0)
    expect(resolved.state.followers.B.enabled).toBe(false)
    expect(resolved.state.followers.B.phase).toBe("unavailable")
  })
})

describe("tempo sync reducer: mode-branching phase-offset-observed", () => {
  function engagedAndAligned(mode: "beat" | "tempo") {
    const { state, effects } = apply(initialTempoSyncState(), [
      playing("A"),
      ready("A", 120),
      ready("B", 120),
      { type: "toggle-sync", deck: "B", mode },
    ])
    const requestId = effects[effects.length - 1].requestId
    return reduceTempoSync(state, { type: "align-result", deck: "B", requestId, outcome: "ok" }).state
  }

  it("BeatSync re-aligns once drift passes the threshold", () => {
    const state = engagedAndAligned("beat")
    expect(state.followers.B.phase).toBe("aligned")

    const result = reduceTempoSync(state, {
      type: "phase-offset-observed",
      deck: "B",
      offsetBeats: BEAT_SYNC_DRIFT_THRESHOLD_BEATS + 0.01,
      at: 10,
    })
    expect(result.effects).toHaveLength(1)
    expect(result.effects[0]).toMatchObject({ type: "align-follower", deck: "B", mode: "beat" })
    expect(result.state.followers.B.phaseOffsetBeats).toBeCloseTo(BEAT_SYNC_DRIFT_THRESHOLD_BEATS + 0.01)
  })

  it("BeatSync does not re-align below the drift threshold", () => {
    const state = engagedAndAligned("beat")
    const result = reduceTempoSync(state, {
      type: "phase-offset-observed",
      deck: "B",
      offsetBeats: BEAT_SYNC_DRIFT_THRESHOLD_BEATS / 2,
      at: 10,
    })
    expect(result.effects).toHaveLength(0)
  })

  it("BeatSync honours the cooldown between corrections", () => {
    const state = engagedAndAligned("beat")
    const first = reduceTempoSync(state, {
      type: "phase-offset-observed",
      deck: "B",
      offsetBeats: 0.5,
      at: 10,
    })
    expect(first.effects).toHaveLength(1)

    const withResult = reduceTempoSync(first.state, {
      type: "align-result",
      deck: "B",
      requestId: first.effects[0].requestId,
      outcome: "ok",
    }).state

    const tooSoon = reduceTempoSync(withResult, {
      type: "phase-offset-observed",
      deck: "B",
      offsetBeats: 0.5,
      at: 10 + BEAT_SYNC_REALIGN_COOLDOWN_SECONDS - 0.1,
    })
    expect(tooSoon.effects).toHaveLength(0)

    const afterCooldown = reduceTempoSync(tooSoon.state, {
      type: "phase-offset-observed",
      deck: "B",
      offsetBeats: 0.5,
      at: 10 + BEAT_SYNC_REALIGN_COOLDOWN_SECONDS + 0.1,
    })
    expect(afterCooldown.effects).toHaveLength(1)
  })

  it("TempoSync only updates the phase-offset readout and never re-aligns", () => {
    const state = engagedAndAligned("tempo")
    const result = reduceTempoSync(state, {
      type: "phase-offset-observed",
      deck: "B",
      offsetBeats: 5,
      at: 10,
    })
    expect(result.effects).toHaveLength(0)
    expect(result.state.followers.B.phaseOffsetBeats).toBe(5)
    expect(result.state.followers.B.phase).toBe("aligned")
    expect(result.state.followers.B.lastAlignAt).toBeNull()
  })
})

describe("tempo sync reducer: idempotent redundant facts", () => {
  it("dispatching the same deck-transport fact twice reassigns master exactly once", () => {
    const initial = initialTempoSyncState()
    const first = reduceTempoSync(initial, playing("A"))
    expect(first.state.master).toBe("A")

    const second = reduceTempoSync(first.state, playing("A"))
    expect(second.state).toBe(first.state)
    expect(second.effects).toHaveLength(0)
  })

  it("dispatching the same phase-offset-observed fact twice never produces two align effects", () => {
    const { state } = apply(initialTempoSyncState(), [
      playing("A"),
      ready("A", 120),
      ready("B", 120),
      { type: "toggle-sync", deck: "B", mode: "beat" },
    ])
    const requestId = state.followers.B.inFlightRequestId!
    const afterFirstAlign = reduceTempoSync(state, {
      type: "align-result",
      deck: "B",
      requestId,
      outcome: "ok",
    }).state

    const fact: TempoSyncEvent = { type: "phase-offset-observed", deck: "B", offsetBeats: 0.5, at: 20 }
    const first = reduceTempoSync(afterFirstAlign, fact)
    const second = reduceTempoSync(first.state, fact)

    const totalEffects = first.effects.length + second.effects.length
    expect(totalEffects).toBe(1)
    expect(first.effects).toHaveLength(1)
    expect(second.effects).toHaveLength(0)
  })

  it("dispatching the same align-result twice is a no-op the second time", () => {
    const { state } = apply(initialTempoSyncState(), [
      playing("A"),
      ready("A", 120),
      ready("B", 120),
      { type: "toggle-sync", deck: "B", mode: "beat" },
    ])
    const requestId = state.followers.B.inFlightRequestId!
    const fact: TempoSyncEvent = { type: "align-result", deck: "B", requestId, outcome: "ok" }

    const first = reduceTempoSync(state, fact)
    expect(first.state.followers.B.phase).toBe("aligned")

    const second = reduceTempoSync(first.state, fact)
    expect(second.state).toBe(first.state)
  })

  it("ignores a stale align-result once a newer alignment is already in flight", () => {
    const { state } = apply(initialTempoSyncState(), [
      playing("A"),
      ready("A", 120),
      ready("B", 120),
      { type: "toggle-sync", deck: "B", mode: "beat" },
    ])
    const firstRequestId = state.followers.B.inFlightRequestId!

    const afterFirstAlign = reduceTempoSync(state, {
      type: "align-result",
      deck: "B",
      requestId: firstRequestId,
      outcome: "ok",
    }).state

    const reArmed = reduceTempoSync(afterFirstAlign, {
      type: "phase-offset-observed",
      deck: "B",
      offsetBeats: 0.5,
      at: 20,
    })
    const secondRequestId = reArmed.state.followers.B.inFlightRequestId!
    expect(secondRequestId).not.toBe(firstRequestId)

    const staleResult = reduceTempoSync(reArmed.state, {
      type: "align-result",
      deck: "B",
      requestId: firstRequestId,
      outcome: "error",
      reason: "stale",
    })

    expect(staleResult.state).toBe(reArmed.state)
    expect(staleResult.state.followers.B.inFlightRequestId).toBe(secondRequestId)
    expect(staleResult.state.followers.B.phase).toBe("pending")
  })
})

describe("tempo sync reducer: master toggling its own SYNC", () => {
  it("marks the master deck aligned immediately with no align-follower effect", () => {
    const { state, effects } = apply(initialTempoSyncState(), [
      playing("A"),
      ready("A", 120),
      { type: "toggle-sync", deck: "A", mode: "beat" },
    ])
    expect(effects).toHaveLength(0)
    expect(state.followers.A.enabled).toBe(true)
    expect(state.followers.A.phase).toBe("aligned")
  })

  it("toggling the same mode again disengages SYNC", () => {
    const { state } = apply(initialTempoSyncState(), [
      playing("A"),
      ready("A", 120),
      { type: "toggle-sync", deck: "A", mode: "beat" },
      { type: "toggle-sync", deck: "A", mode: "beat" },
    ])
    expect(state.followers.A.enabled).toBe(false)
    expect(state.followers.A.phase).toBe("off")
  })
})
