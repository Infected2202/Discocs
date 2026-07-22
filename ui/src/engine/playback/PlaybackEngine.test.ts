import { beforeEach, describe, expect, it, vi } from "vitest"
import { PlaybackEngine } from "./PlaybackEngine"
import { createFakeStretchNode, FakeAudioContext } from "./testing/webAudioFakes"
import type { StretchNode } from "./signalsmith/types"
import type { DecodedTimeline } from "@/engine/timeline/types"

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

describe("PlaybackEngine Phase 1 routing", () => {
  beforeEach(() => {
    FakeAudioContext.instances.length = 0
    vi.stubGlobal("AudioWorkletNode", class AudioWorkletNode {})
  })

  it("routes replacement media through one neutral Deck A graph", async () => {
    vi.stubGlobal("AudioContext", FakeAudioContext)
    const engine = new PlaybackEngine()
    const first = document.createElement("audio")
    const second = document.createElement("audio")

    expect(engine.routeProgramElement(first)).toBe(true)
    expect(engine.routeProgramElement(second)).toBe(true)
    await engine.ensureReady()

    const context = FakeAudioContext.instances[0]!
    expect(FakeAudioContext.instances).toHaveLength(1)
    expect(context.mediaElements).toEqual([first, second])
    expect(context.resume).toHaveBeenCalledTimes(1)
    expect(context.mediaNodes[0]?.disconnect).toHaveBeenCalled()
    expect(engine.getSnapshot().mixer).toMatchObject({
      crossfader: 0,
      trims: { A: 0.5, B: 0.5 },
      eq: { A: { low: 0.5, mid: 0.5, high: 0.5 }, B: { low: 0.5, mid: 0.5, high: 0.5 } },
    })
  })

  it("does not create a second media source when DJ activation routes the same element", () => {
    vi.stubGlobal("AudioContext", FakeAudioContext)
    const engine = new PlaybackEngine()
    const media = document.createElement("audio")

    expect(engine.routeProgramElement(media, 1, "q1")).toBe(true)
    expect(engine.routeProgramElement(media, 1, "q1")).toBe(true)

    expect(FakeAudioContext.instances[0]?.mediaElements).toEqual([media])
  })

  it("propagates context-resume failure and never calls media playback itself", async () => {
    class FailingContext extends FakeAudioContext {
      override resume = vi.fn(async () => { throw new Error("gesture required") })
    }
    vi.stubGlobal("AudioContext", FailingContext)
    const engine = new PlaybackEngine()
    const media = document.createElement("audio")
    const play = vi.spyOn(media, "play")
    engine.routeProgramElement(media)

    await expect(engine.ensureReady()).rejects.toThrow("gesture required")
    expect(play).not.toHaveBeenCalled()
  })

  it("falls back to ordinary HTML media when Web Audio is unavailable", () => {
    vi.stubGlobal("AudioContext", undefined)
    const engine = new PlaybackEngine()

    expect(engine.routeProgramElement(document.createElement("audio"))).toBe(false)
    expect(FakeAudioContext.instances).toHaveLength(0)
  })

  it("keeps both sources attached through handover and retires outgoing only after confirmation", async () => {
    vi.stubGlobal("AudioContext", FakeAudioContext)
    const engine = new PlaybackEngine()
    const outgoing = document.createElement("audio")
    const incoming = document.createElement("audio")
    engine.routeProgramElement(outgoing, 1, "queue-1")
    const incomingDeck = engine.routeIncomingElement(incoming, 2, "queue-2")

    const result = await engine.handover({ incomingDeck: incomingDeck!, clientHandoverId: "h-1" })
    const context = FakeAudioContext.instances[0]!
    expect(result).toMatchObject({ outgoingDeck: "A", programDeck: "B" })
    expect(engine.getSnapshot()).toMatchObject({
      programDeck: "B",
      decks: { B: { trackId: 2, queueItemId: "queue-2", role: "program" } },
    })
    expect(context.mediaNodes[0]?.disconnect).not.toHaveBeenCalled()

    await engine.confirmRetirement("A")
    expect(context.mediaNodes[0]?.disconnect).toHaveBeenCalled()
    expect(context.mediaNodes[1]?.disconnect).not.toHaveBeenCalled()
  })

  it("projects external media transport state and publishes media changes", () => {
    vi.stubGlobal("AudioContext", FakeAudioContext)
    const engine = new PlaybackEngine()
    const media = document.createElement("audio")
    Object.defineProperty(media, "duration", { configurable: true, value: 180 })
    Object.defineProperty(media, "currentTime", { configurable: true, value: 24, writable: true })
    Object.defineProperty(media, "paused", { configurable: true, value: true })
    const listener = vi.fn()
    engine.subscribe(listener)

    engine.routeProgramElement(media, 9, "queue-9")
    expect(engine.getSnapshot().decks.A).toMatchObject({
      transport: "paused",
      duration: 180,
      anchor: { mediaSeconds: 24, rate: 1 },
    })

    Object.defineProperty(media, "paused", { configurable: true, value: false })
    media.dispatchEvent(new Event("play"))
    expect(engine.getSnapshot().decks.A.transport).toBe("playing")
    expect(listener).toHaveBeenCalledTimes(2)

    Object.defineProperty(media, "currentTime", { configurable: true, value: 48, writable: true })
    media.dispatchEvent(new Event("seeked"))
    expect(engine.getSnapshot().decks.A.anchor?.mediaSeconds).toBe(48)
    expect(listener).toHaveBeenCalledTimes(3)
  })

  it("keeps master ownership on the playing decks and returns to clock only when both stop", async () => {
    vi.stubGlobal("AudioContext", FakeAudioContext)
    const engine = new PlaybackEngine()
    const mediaA = document.createElement("audio")
    const mediaB = document.createElement("audio")
    Object.defineProperty(mediaA, "paused", { configurable: true, value: false })
    Object.defineProperty(mediaB, "paused", { configurable: true, value: true })
    engine.routeProgramElement(mediaA, 9, "queue-9")
    engine.routeIncomingElement(mediaB, 10, "queue-10")

    // DJ activation routes an already-playing ordinary <audio>, so no new
    // `play` event will arrive after the engine attaches its listeners.
    expect(engine.getSnapshot().tempoSync).toMatchObject({ auto: true, master: "A" })
    await expect(engine.setClockMaster()).rejects.toThrow("unavailable while a deck is playing")

    Object.defineProperty(mediaB, "paused", { configurable: true, value: false })
    mediaB.dispatchEvent(new Event("play"))
    expect(engine.getSnapshot().tempoSync.master).toBe("A")

    // setTempoMaster now dispatches R1's set-deck-master command, which is a
    // genuine manual pin (auto: false) -- unlike the pre-rewrite engine,
    // which always left `auto: true` even for an explicit master pin,
    // blurring "auto-promoted" and "manually forced" into the same flag.
    // That blur is exactly the kind of scattered-mutation inconsistency this
    // rewrite exists to remove, so pausing the pinned master must NOT
    // auto-fall-back until AUTO is explicitly re-enabled.
    await engine.setTempoMaster("B")
    expect(engine.getSnapshot().tempoSync).toMatchObject({ master: "B", auto: false })

    Object.defineProperty(mediaB, "paused", { configurable: true, value: true })
    mediaB.dispatchEvent(new Event("pause"))
    expect(engine.getSnapshot().tempoSync.master).toBe("B")

    await engine.setAutoMaster()
    expect(engine.getSnapshot().tempoSync).toMatchObject({ auto: true, master: "A" })

    Object.defineProperty(mediaA, "paused", { configurable: true, value: true })
    mediaA.dispatchEvent(new Event("pause"))
    expect(engine.getSnapshot().tempoSync.master).toBe("clock")
  })

  it("projects ended and failed external media states", () => {
    vi.stubGlobal("AudioContext", FakeAudioContext)
    const engine = new PlaybackEngine()
    const media = document.createElement("audio")
    engine.routeProgramElement(media)

    Object.defineProperty(media, "ended", { configurable: true, value: true })
    expect(engine.getSnapshot().decks.A.transport).toBe("ended")

    Object.defineProperty(media, "error", { configurable: true, value: { code: 3 } })
    expect(engine.getSnapshot().decks.A.transport).toBe("error")
  })

  it("upgrades a fully buffered eligible deck to Signalsmith and keeps native fallback otherwise", async () => {
    vi.stubGlobal("AudioContext", FakeAudioContext)
    const eligible = vi.fn().mockResolvedValue({ ready: true, reason: null })
    const engine = new PlaybackEngine(undefined, {
      stretch: { createNode: vi.fn(async (context: AudioContext) => createFakeStretchNode(context)) },
      stretchEligibility: eligible,
    })
    const media = document.createElement("audio")
    engine.routeProgramElement(media, 9, "queue-9")

    await expect(engine.upgradeDeckSource("A", {
      url: "blob:track-9",
      trackId: 9,
      queueItemId: "queue-9",
      blob: new Blob(["encoded"]),
    })).resolves.toEqual({ upgraded: true, kind: "signalsmith", reason: null })

    expect(eligible).toHaveBeenCalledWith(9)
    expect(engine.getSnapshot().decks.A).toMatchObject({
      sourceKind: "signalsmith",
      tempoMode: "pitch-preserving",
      buffered: [{ start: 0, end: 2 }],
    })

    const fallback = new PlaybackEngine(undefined, {
      stretchEligibility: vi.fn().mockResolvedValue({ ready: false, reason: "Timeline is missing" }),
    })
    const fallbackMedia = document.createElement("audio")
    fallback.routeProgramElement(fallbackMedia, 10, "queue-10")
    await expect(fallback.upgradeDeckSource("A", {
      url: "blob:track-10", trackId: 10, blob: new Blob(["encoded"]),
    })).resolves.toEqual({ upgraded: false, kind: "media-element", reason: "Timeline is missing" })
    expect(fallback.getSnapshot().decks.A).toMatchObject({
      sourceKind: "media-element",
      tempoMode: "native",
      degradedReason: "Timeline is missing",
    })
  })

  it("assigns the first playing deck as master and drives an engaged follower", async () => {
    vi.stubGlobal("AudioContext", FakeAudioContext)
    let nodeA!: StretchNode
    let nodeB!: StretchNode
    const createNode = vi.fn(async (context: AudioContext) => {
      if (!nodeA) {
        nodeA = createFakeStretchNode(context, { inputTime: 0.75 })
        return nodeA
      }
      nodeB = createFakeStretchNode(context, { inputTime: 1.4 })
      return nodeB
    })
    const timelines: Record<number, DecodedTimeline> = {
      1: {
        durationSeconds: 2, levels: [], bpm: 126, beatConfidence: 0.9, rhythmCoverageSeconds: 2,
        beats: new Float32Array([0, 0.5, 1, 1.5]), localTempo: new Float32Array([126, 126, 126, 126]),
      },
      2: {
        durationSeconds: 2, levels: [], bpm: 120, beatConfidence: 0.9, rhythmCoverageSeconds: 2,
        beats: new Float32Array([0.1, 0.6, 1.1, 1.6]), localTempo: new Float32Array([120, 120, 120, 120]),
      },
    }
    const engine = new PlaybackEngine(undefined, {
      stretch: { createNode },
      stretchEligibility: vi.fn(async (trackId) => ({ ready: true, reason: null, timeline: timelines[trackId] })),
    })
    engine.routeProgramElement(document.createElement("audio"), 1, "q1")
    const incomingMedia = document.createElement("audio")
    incomingMedia.playbackRate = 1.03
    engine.routeIncomingElement(incomingMedia, 2, "q2")
    await engine.upgradeDeckSource("A", { url: "blob:a", trackId: 1, blob: new Blob(["a"]) })

    await engine.playDeck("A")
    expect(engine.getSnapshot().tempoSync).toMatchObject({ auto: true, master: "A", clockBpm: 126 })

    await engine.toggleSync("A", "beat")
    expect(engine.getSnapshot().tempoSync.decks.A).toMatchObject({ enabled: true, phase: "aligned", reason: null })

    // Deck B has never had upgradeDeckSource called on it yet, so it is not
    // merely "not ready" (which would arm) -- SYNC feasibility is checked
    // synchronously at arm-time per SYNC_REWRITE_PLAN.md §2, and an
    // unattempted upgrade is infeasible right now, so the request is
    // rejected synchronously: enabled stays false. This inverts the
    // pre-rewrite assertion here, which asserted `enabled: true,
    // phase: "unavailable"` -- a button that visually "engages" but can
    // never actually align is exactly the "lights up, does nothing" bug
    // this rewrite exists to fix.
    await engine.toggleSync("B", "beat")
    expect(engine.getSnapshot().tempoSync.decks.B).toMatchObject({
      enabled: false,
      phase: "unavailable",
      reason: "Deck B will never produce a beat timeline",
    })
    await engine.upgradeDeckSource("B", { url: "blob:b", trackId: 2, blob: new Blob(["b"]) })
    expect(engine.getSnapshot().decks.B.tempoRatio).toBeCloseTo(1.03)
    expect(vi.mocked(nodeB.schedule)).toHaveBeenCalledWith(expect.objectContaining({ rate: 1.03 }), true)

    // Deck B is now feasible and ready; pressing SYNC again (there is no
    // more standalone `synchronizeDeck` -- toggling is the only entry point
    // now) succeeds and arms it as a follower of A.
    await engine.toggleSync("B", "beat")
    expect(engine.getSnapshot()).toMatchObject({
      tempoSync: { decks: { B: { enabled: true, phase: "aligned", reason: null } } },
      decks: { B: { tempoRatio: 1.05 } },
    })
    expect(vi.mocked(nodeB.schedule)).toHaveBeenCalledWith(expect.objectContaining({ rate: 1.05 }), true)
    expect(vi.mocked(nodeB.schedule)).toHaveBeenCalledWith(expect.objectContaining({
      active: false,
      input: expect.any(Number),
      rate: 1.05,
    }))

    await engine.setTempo("A", 1.02)
    expect(engine.getSnapshot().decks.B.tempoRatio).toBeCloseTo(1.071)
    await expect(engine.setTempo("B", 1)).rejects.toThrow("controlled by the tempo master")
    await engine.toggleSync("B", "beat")
    expect(engine.getSnapshot().decks.B.tempoRatio).toBeCloseTo(1.071)
    expect(engine.getSnapshot().tempoSync.decks.B).toMatchObject({ enabled: false, phase: "off" })
    await engine.toggleSync("B", "beat")

    const scheduledBeforeRecovery = vi.mocked(nodeB.schedule).mock.calls.length
    await engine.playDeck("B")
    await engine.seekDeck("B", 1.2)
    await engine.setLoop("B", { enabled: true, startSeconds: 0.6, endSeconds: 1.6 })
    expect(vi.mocked(nodeB.schedule).mock.calls.length).toBeGreaterThan(scheduledBeforeRecovery)
    expect(vi.mocked(nodeB.schedule)).toHaveBeenCalledWith(expect.objectContaining({
      loopStart: 0.6,
      loopEnd: 1.6,
    }))

    await engine.pauseDeck("A")
    expect(engine.getSnapshot().tempoSync).toMatchObject({
      auto: true,
      master: "B",
      decks: {
        A: { enabled: true, phase: "aligned" },
        B: { enabled: true, phase: "aligned" },
      },
    })
    await engine.handover({ incomingDeck: "B", clientHandoverId: "sync-handover" })
    expect(engine.getSnapshot()).toMatchObject({ programDeck: "B", tempoSync: { master: "B" } })
  })

  it("collapses a redundant DeckRuntime transport notification into a single alignment", async () => {
    vi.stubGlobal("AudioContext", FakeAudioContext)
    let nodeA!: StretchNode
    let nodeB!: StretchNode
    const createNode = vi.fn(async (context: AudioContext) => {
      if (!nodeA) {
        nodeA = createFakeStretchNode(context, { inputTime: 0 })
        return nodeA
      }
      nodeB = createFakeStretchNode(context, { inputTime: 0 })
      return nodeB
    })
    const timeline: DecodedTimeline = {
      durationSeconds: 4, levels: [], bpm: 120, beatConfidence: 0.9, rhythmCoverageSeconds: 4,
      beats: new Float32Array([0, 0.5, 1, 1.5, 2]), localTempo: new Float32Array([120, 120, 120, 120, 120]),
    }
    const engine = new PlaybackEngine(undefined, {
      stretch: { createNode },
      stretchEligibility: vi.fn(async () => ({ ready: true, reason: null, timeline })),
    })
    engine.routeProgramElement(document.createElement("audio"), 1, "q1")
    engine.routeIncomingElement(document.createElement("audio"), 2, "q2")
    await engine.upgradeDeckSource("A", { url: "blob:a", trackId: 1, blob: new Blob(["a"]) })
    await engine.upgradeDeckSource("B", { url: "blob:b", trackId: 2, blob: new Blob(["b"]) })

    await engine.playDeck("A")
    await engine.toggleSync("B", "beat")
    expect(engine.getSnapshot().tempoSync.decks.B).toMatchObject({ enabled: true, phase: "aligned" })

    // Starting B goes through two independent trigger paths for the exact
    // same fact: playDeck's own explicit dispatch, and (because alignFollower
    // calls runtime.play() as part of that same effect) DeckRuntime's own
    // notify callback firing again afterward. Pre-rewrite this pairing was
    // guarded by an `explicitTransportCommands` suppression Set; R1's
    // reducer makes redundant facts structurally idempotent no-ops instead,
    // so this must produce exactly one align-triggered "start" (one
    // schedule call with active: true), not two.
    const startCallsBefore = vi.mocked(nodeB.schedule).mock.calls
      .filter(([change]) => change.active === true).length

    await engine.playDeck("B")

    const startCallsAfter = vi.mocked(nodeB.schedule).mock.calls
      .filter(([change]) => change.active === true).length
    expect(startCallsAfter - startCallsBefore).toBe(1)
    expect(engine.getSnapshot().tempoSync.decks.B.phase).toBe("aligned")
    expect(engine.getSnapshot().tempoSync.master).toBe("A")
  })

  // Named regression test for SYNC_REWRITE_PLAN.md R7 (§2.4/§3/§5): SYNC is
  // armed on deck B while deck A plays, Play is pressed on B before its
  // Signalsmith upgrade resolves (the worklet's createNode is deliberately
  // left pending here to simulate a slow cold-start), fake/real time is
  // allowed to advance, and deck B must genuinely start playing and end up
  // phase-aligned rather than hang. Before R7's DeckRuntime timeout +
  // StretchDeckSource abortRace existed, a worklet RPC stalling here (as
  // opposed to merely being slow, like this test's deferred promise) had no
  // way to ever reject -- DeckRuntime.load() would await it forever and this
  // exact scenario would never settle.
  it("starts and phase-aligns deck B once its Signalsmith upgrade resolves, after SYNC was armed mid-upgrade", async () => {
    vi.stubGlobal("AudioContext", FakeAudioContext)
    let nodeA!: StretchNode
    let contextForB: AudioContext | undefined
    const nodeBReady = deferred<StretchNode>()
    const createNode = vi.fn(async (context: AudioContext) => {
      if (!nodeA) {
        nodeA = createFakeStretchNode(context, { inputTime: 0 })
        return nodeA
      }
      contextForB = context
      return nodeBReady.promise
    })
    const timelines: Record<number, DecodedTimeline> = {
      1: {
        durationSeconds: 4, levels: [], bpm: 126, beatConfidence: 0.9, rhythmCoverageSeconds: 4,
        beats: new Float32Array([0, 0.5, 1, 1.5, 2]), localTempo: new Float32Array([126, 126, 126, 126, 126]),
      },
      2: {
        durationSeconds: 4, levels: [], bpm: 124, beatConfidence: 0.9, rhythmCoverageSeconds: 4,
        beats: new Float32Array([0.1, 0.6, 1.1, 1.6, 2.1]), localTempo: new Float32Array([124, 124, 124, 124, 124]),
      },
    }
    const engine = new PlaybackEngine(undefined, {
      stretch: { createNode },
      stretchEligibility: vi.fn(async (trackId) => ({ ready: true, reason: null, timeline: timelines[trackId] })),
    })

    engine.routeProgramElement(document.createElement("audio"), 1, "q1")
    engine.routeIncomingElement(document.createElement("audio"), 2, "q2")
    await engine.upgradeDeckSource("A", { url: "blob:a", trackId: 1, blob: new Blob(["a"]) })
    await engine.playDeck("A")
    expect(engine.getSnapshot().tempoSync.master).toBe("A")

    // The "Play" press on deck B: an autoplay upgrade whose Signalsmith
    // worklet creation is left pending (nodeBReady is not resolved yet).
    const upgradeB = engine.upgradeDeckSource(
      "B",
      { url: "blob:b", trackId: 2, blob: new Blob(["b"]) },
      { autoplay: true },
    )

    // Wait for upgradeDeckSource's own optimistic "known feasible, not yet
    // ready" capability fact to land before arming SYNC -- otherwise this
    // toggle races the fact and can see the pre-upgrade "infeasible" state.
    await vi.waitFor(() => {
      expect(engine.getSnapshot().tempoSync.decks.B.canEngageBeatSync).toBe(true)
    })

    // SYNC engages while deck B is still mid-upgrade: known-feasible but not
    // ready, so it arms rather than rejecting or (pre-rewrite) lying about
    // being engaged.
    await engine.toggleSync("B", "beat")
    expect(engine.getSnapshot().tempoSync.decks.B).toMatchObject({ enabled: true, phase: "arming" })

    // Confirm the worklet creation call for deck B actually happened (and is
    // the thing left hanging) before releasing it.
    await vi.waitFor(() => expect(contextForB).toBeDefined())
    nodeBReady.resolve(createFakeStretchNode(contextForB!, { inputTime: 0 }))
    await upgradeB

    await vi.waitFor(() => {
      expect(engine.getSnapshot().tempoSync.decks.B.phase).toBe("aligned")
    })
    expect(engine.getSnapshot().decks.B.transport).toBe("playing")
    expect(engine.getSnapshot().tempoSync.decks.B.reason).toBeNull()
  })

  it("keeps the editable master clock selected when AUTO is disabled", async () => {
    vi.stubGlobal("AudioContext", FakeAudioContext)
    const engine = new PlaybackEngine()
    await engine.ensureReady()

    await engine.setClockMaster()
    await engine.setClockTempo(140)

    expect(engine.getSnapshot().tempoSync).toMatchObject({ auto: false, master: "clock", clockBpm: 140 })
  })

  // R4 (SYNC_REWRITE_PLAN.md §2.1): the Signalsmith 0.25s clock tick
  // (StretchDeckSource's setUpdateInterval callback, reached here via
  // DeckRuntime's dedicated onClockTick channel) must compute the
  // follower's beat-phase offset from the master and dispatch
  // phase-offset-observed. Mode determines what happens next: BeatSync
  // re-aligns once past the drift threshold and honours the cooldown;
  // TempoSync only ever updates the phaseOffsetBeats readout.
  //
  // The master here is the plain clock (not another deck): masterPhaseAt
  // for "clock" is a pure function of audioTime with no Signalsmith
  // scheduling-lead of its own, which keeps the expected numbers in this
  // fake deterministic instead of entangled with a second deck's lead time.
  describe("mode-aware alignment driven by the Signalsmith clock tick", () => {
    function beatTimeline(): DecodedTimeline {
      const beats = Float32Array.from({ length: 32 }, (_, index) => index * 0.5)
      return {
        durationSeconds: 16, levels: [], bpm: 120, beatConfidence: 0.9, rhythmCoverageSeconds: 16,
        beats, localTempo: new Float32Array(32).fill(120),
      }
    }

    async function setUpClockFollower(mode: "beat" | "tempo") {
      vi.stubGlobal("AudioContext", FakeAudioContext)
      let nodeB!: StretchNode
      const createNode = vi.fn(async (context: AudioContext) => {
        nodeB = createFakeStretchNode(context, { inputTime: 0 })
        return nodeB
      })
      const engine = new PlaybackEngine(undefined, {
        stretch: { createNode },
        stretchEligibility: vi.fn(async () => ({ ready: true, reason: null, timeline: beatTimeline() })),
      })
      engine.routeProgramElement(document.createElement("audio"), 1, "q1")
      engine.routeIncomingElement(document.createElement("audio"), 2, "q2")
      // FakeAudioContext.decodeAudioData defaults to a 2s buffer, but this
      // suite advances the context clock well past that (context.advance(5)
      // below, plus another 3s mid-test) to reach steady linear follower
      // motion. Left at the default, StretchDeckSource's own end-of-track
      // detection (handleClockUpdate: inputTime >= duration - 0.05) would
      // fire on the very first manual tick(), stopping deck B and making it
      // report "ended" -- which makes dispatchPhaseOffsetObserved's own
      // (correct) not-playing guard bail out before ever computing a phase
      // offset. Match the fake decode's duration to the beat timeline's own
      // duration so the deck genuinely stays "playing" for the clock
      // advances this suite performs.
      vi.mocked(FakeAudioContext.instances[0]!.decodeAudioData).mockResolvedValue({
        duration: beatTimeline().durationSeconds,
        length: 4,
        numberOfChannels: 2,
        getChannelData: (channel: number) => new Float32Array(channel === 0 ? [1, 2, 3, 4] : [5, 6, 7, 8]),
      } as unknown as AudioBuffer)
      await engine.setClockTempo(120)
      // Pin the master clock (auto: false) before B ever plays -- otherwise
      // AUTO would promote B itself to master the instant it starts, which
      // would defeat the whole point of using the clock as a bias-free
      // master for this test.
      await engine.setClockMaster()
      await engine.upgradeDeckSource("B", { url: "blob:b", trackId: 2, blob: new Blob(["b"]) })
      await engine.toggleSync("B", mode)
      await engine.playDeck("B")
      expect(engine.getSnapshot().tempoSync).toMatchObject({ master: "clock" })
      expect(engine.getSnapshot().tempoSync.decks.B).toMatchObject({ enabled: true, mode, phase: "aligned" })

      const context = FakeAudioContext.instances[0]!
      // Past the Signalsmith scheduling lead (~0.14s): position tracking has
      // settled into steady linear motion, matching real-world behaviour
      // once Web Audio's own small scheduling margin has elapsed. At this
      // point the follower is genuinely phase-locked (offset 0), which the
      // sub-threshold test below relies on as its clean baseline.
      context.advance(5)
      const tick = vi.mocked(nodeB.setUpdateInterval).mock.calls[0][1]!
      return { engine, context, nodeB, tick }
    }

    // Directly perturbs the fake node's tracked position to simulate
    // real-world clock drift this deterministic fake would never accumulate
    // on its own (follower rate is exactly locked to the clock, so nothing
    // in the model naturally drifts) -- 0.1s at 120bpm is 0.2 beats, well
    // past BEAT_SYNC_DRIFT_THRESHOLD_BEATS (0.06). Absolute (relative to the
    // clean, unperturbed `audioTime`-equals-position baseline this setup
    // produces), not relative to whatever the node currently reads, so
    // repeated calls in a single test represent a fresh drift each time
    // rather than compounding an ever-growing one.
    async function perturb(nodeB: StretchNode, context: FakeAudioContext, offsetSeconds: number) {
      await nodeB.schedule({
        active: true, input: context.currentTime + offsetSeconds, output: context.currentTime, rate: 1,
      })
    }

    it("BeatSync re-aligns once drift passes the threshold, then honours the cooldown, then re-aligns again after it", async () => {
      const { engine, context, nodeB, tick } = await setUpClockFollower("beat")

      const callsBeforeFirstTick = vi.mocked(nodeB.schedule).mock.calls.length
      await perturb(nodeB, context, 0.1)
      tick(nodeB.inputTime)

      // +1 for our manual perturbation call, +2 for the correction's
      // setRate/seek (each of which issues one node.schedule call).
      expect(vi.mocked(nodeB.schedule).mock.calls.length).toBe(callsBeforeFirstTick + 3)
      expect(engine.getSnapshot().tempoSync.decks.B.phase).toBe("aligned")

      // Same instant, still drifted: cooldown suppresses a second correction.
      await perturb(nodeB, context, 0.1)
      const callsBeforeCooldownTick = vi.mocked(nodeB.schedule).mock.calls.length
      tick(nodeB.inputTime)
      expect(vi.mocked(nodeB.schedule).mock.calls.length).toBe(callsBeforeCooldownTick)

      // Past the cooldown window: the same drift now corrects again.
      context.advance(3)
      await perturb(nodeB, context, 0.1)
      const callsBeforeReleasedTick = vi.mocked(nodeB.schedule).mock.calls.length
      tick(nodeB.inputTime)
      expect(vi.mocked(nodeB.schedule).mock.calls.length).toBe(callsBeforeReleasedTick + 2)
    })

    it("BeatSync does not re-align for sub-threshold drift", async () => {
      const { engine, context, nodeB, tick } = await setUpClockFollower("beat")

      // 0.02s at 120bpm is 0.04 beats, comfortably under the 0.06 threshold.
      const callsBeforeTick = vi.mocked(nodeB.schedule).mock.calls.length
      await perturb(nodeB, context, 0.02)
      tick(nodeB.inputTime)

      // Only our own manual perturbation call shows up -- no correction.
      expect(vi.mocked(nodeB.schedule).mock.calls.length).toBe(callsBeforeTick + 1)
      expect(engine.getSnapshot().tempoSync.decks.B.phaseOffsetBeats).toBeCloseTo(0.04, 2)
    })

    it("TempoSync never auto-realigns on the clock tick, only updates the phase-offset readout", async () => {
      const { engine, context, nodeB, tick } = await setUpClockFollower("tempo")

      const callsBeforeTick = vi.mocked(nodeB.schedule).mock.calls.length
      await perturb(nodeB, context, 0.1)
      tick(nodeB.inputTime)

      // Only our own manual perturbation call shows up -- the tick produced
      // no setRate/seek correction at all, unlike BeatSync above.
      expect(vi.mocked(nodeB.schedule).mock.calls.length).toBe(callsBeforeTick + 1)
      expect(engine.getSnapshot().tempoSync.decks.B.phaseOffsetBeats).toBeCloseTo(0.2, 2)
      expect(engine.getSnapshot().tempoSync.decks.B.phase).toBe("aligned")

      // Repeating well past the cooldown window still never re-aligns --
      // TempoSync has no such thing, phase is allowed to drift by design.
      context.advance(10)
      const callsBeforeSecondTick = vi.mocked(nodeB.schedule).mock.calls.length
      await perturb(nodeB, context, 0.1)
      tick(nodeB.inputTime)
      expect(vi.mocked(nodeB.schedule).mock.calls.length).toBe(callsBeforeSecondTick + 1)
      expect(engine.getSnapshot().tempoSync.decks.B.phaseOffsetBeats).toBeCloseTo(0.2, 2)
    })
  })

  // R4: beginTempoNudge/endTempoNudge implement the manual pitch-bend
  // correction for an engaged TempoSync follower -- a live setRate nudge
  // while held, snapping back to the locked ratio on release.
  describe("beginTempoNudge / endTempoNudge", () => {
    function loopTimeline(bpm: number): DecodedTimeline {
      return {
        durationSeconds: 8, levels: [], bpm, beatConfidence: 0.9, rhythmCoverageSeconds: 8,
        beats: new Float32Array([0, 0.5, 1, 1.5]), localTempo: new Float32Array([bpm, bpm, bpm, bpm]),
      }
    }

    it("nudges the rate while held and snaps back to the locked ratio on release", async () => {
      vi.stubGlobal("AudioContext", FakeAudioContext)
      let nodeA!: StretchNode
      let nodeB!: StretchNode
      const createNode = vi.fn(async (context: AudioContext) => {
        if (!nodeA) {
          nodeA = createFakeStretchNode(context, { inputTime: 0 })
          return nodeA
        }
        nodeB = createFakeStretchNode(context, { inputTime: 0 })
        return nodeB
      })
      const engine = new PlaybackEngine(undefined, {
        stretch: { createNode },
        stretchEligibility: vi.fn(async () => ({ ready: true, reason: null, timeline: loopTimeline(120) })),
      })
      engine.routeProgramElement(document.createElement("audio"), 1, "q1")
      engine.routeIncomingElement(document.createElement("audio"), 2, "q2")
      await engine.upgradeDeckSource("A", { url: "blob:a", trackId: 1, blob: new Blob(["a"]) })
      await engine.upgradeDeckSource("B", { url: "blob:b", trackId: 2, blob: new Blob(["b"]) })
      await engine.playDeck("A")
      await engine.toggleSync("B", "tempo")
      expect(engine.getSnapshot().tempoSync.decks.B).toMatchObject({ enabled: true, mode: "tempo", phase: "aligned" })

      engine.beginTempoNudge("B", "up")
      await Promise.resolve()
      const nudgedRate = vi.mocked(nodeB.schedule).mock.calls.at(-1)?.[0].rate
      expect(nudgedRate).toBeCloseTo(1.02)

      engine.endTempoNudge("B")
      await Promise.resolve()
      const releasedRate = vi.mocked(nodeB.schedule).mock.calls.at(-1)?.[0].rate
      expect(releasedRate).toBeCloseTo(1)
    })

    it("is a safe no-op reported via reportEngineFailure for a deck that is not an engaged TempoSync follower", async () => {
      vi.stubGlobal("AudioContext", FakeAudioContext)
      const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined)
      let nodeA!: StretchNode
      let nodeB!: StretchNode
      const createNode = vi.fn(async (context: AudioContext) => {
        if (!nodeA) {
          nodeA = createFakeStretchNode(context, { inputTime: 0 })
          return nodeA
        }
        nodeB = createFakeStretchNode(context, { inputTime: 0 })
        return nodeB
      })
      const engine = new PlaybackEngine(undefined, {
        stretch: { createNode },
        stretchEligibility: vi.fn(async () => ({ ready: true, reason: null, timeline: loopTimeline(120) })),
      })
      engine.routeProgramElement(document.createElement("audio"), 1, "q1")
      engine.routeIncomingElement(document.createElement("audio"), 2, "q2")
      await engine.upgradeDeckSource("A", { url: "blob:a", trackId: 1, blob: new Blob(["a"]) })
      await engine.upgradeDeckSource("B", { url: "blob:b", trackId: 2, blob: new Blob(["b"]) })
      await engine.playDeck("A")

      // Deck B has no engaged SYNC of any kind yet. Baseline (not a raw
      // "never called" check): upgradeDeckSource's own load() already issued
      // one schedule() call to apply the initial tempoRatio, before beginTempoNudge
      // ever runs -- the no-op must add no *further* calls, not leave the
      // node untouched from the very start.
      const callsBeforeUnengagedNudge = vi.mocked(nodeB.schedule).mock.calls.length
      engine.beginTempoNudge("B", "up")
      expect(errorSpy).toHaveBeenCalledWith(
        expect.stringContaining("beginTempoNudge"),
        expect.any(Error),
      )
      expect(vi.mocked(nodeB.schedule).mock.calls.length).toBe(callsBeforeUnengagedNudge)

      // Deck A is the tempo master itself -- never a nudge target either.
      engine.beginTempoNudge("A", "up")
      expect(errorSpy).toHaveBeenCalledTimes(2)

      // A BeatSync (not TempoSync) follower is also not a valid nudge target.
      await engine.toggleSync("B", "beat")
      expect(engine.getSnapshot().tempoSync.decks.B).toMatchObject({ enabled: true, mode: "beat", phase: "aligned" })
      const callsBeforeInvalidNudge = vi.mocked(nodeB.schedule).mock.calls.length
      engine.beginTempoNudge("B", "up")
      expect(errorSpy).toHaveBeenCalledTimes(3)
      expect(vi.mocked(nodeB.schedule).mock.calls.length).toBe(callsBeforeInvalidNudge)

      // endTempoNudge on a deck that was never begun is a silent no-op.
      engine.endTempoNudge("B")
      expect(errorSpy).toHaveBeenCalledTimes(3)

      errorSpy.mockRestore()
    })
  })
})
