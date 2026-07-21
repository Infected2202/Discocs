import { beforeEach, describe, expect, it, vi } from "vitest"
import { PlaybackEngine } from "./PlaybackEngine"
import type { StretchNode } from "./signalsmith/types"

class FakeParam {
  value = 1
  cancelScheduledValues = vi.fn()
  setValueAtTime = vi.fn((value: number) => { this.value = value })
  linearRampToValueAtTime = vi.fn((value: number) => { this.value = value })
}

class FakeNode {
  gain = new FakeParam()
  frequency = new FakeParam()
  Q = new FakeParam()
  threshold = new FakeParam()
  knee = new FakeParam()
  ratio = new FakeParam()
  attack = new FakeParam()
  release = new FakeParam()
  type = "peaking"
  fftSize = 32
  connect = vi.fn()
  disconnect = vi.fn()
  getFloatTimeDomainData = vi.fn((samples: Float32Array) => samples.fill(0))
}

class FakeContext {
  static instances: FakeContext[] = []
  readonly mediaNodes: FakeNode[] = []
  readonly mediaElements: HTMLMediaElement[] = []
  state: AudioContextState = "suspended"
  currentTime = 3
  destination = new FakeNode()
  resume = vi.fn(async () => { this.state = "running" })
  close = vi.fn(async () => { this.state = "closed" })
  createGain = vi.fn(() => new FakeNode())
  createDynamicsCompressor = vi.fn(() => new FakeNode())
  createBiquadFilter = vi.fn(() => new FakeNode())
  createAnalyser = vi.fn(() => new FakeNode())
  createMediaElementSource(element: HTMLMediaElement) {
    const node = new FakeNode()
    this.mediaNodes.push(node)
    this.mediaElements.push(element)
    return node
  }
  decodeAudioData = vi.fn(async () => ({
    duration: 2,
    length: 4,
    numberOfChannels: 2,
    getChannelData: (channel: number) => new Float32Array(channel === 0 ? [1, 2, 3, 4] : [5, 6, 7, 8]),
  } as AudioBuffer))
  addEventListener = vi.fn()
  removeEventListener = vi.fn()

  constructor() {
    FakeContext.instances.push(this)
  }
}

describe("PlaybackEngine Phase 1 routing", () => {
  beforeEach(() => {
    FakeContext.instances.length = 0
    vi.stubGlobal("AudioWorkletNode", class AudioWorkletNode {})
  })

  it("routes replacement media through one neutral Deck A graph", async () => {
    vi.stubGlobal("AudioContext", FakeContext)
    const engine = new PlaybackEngine()
    const first = document.createElement("audio")
    const second = document.createElement("audio")

    expect(engine.routeProgramElement(first)).toBe(true)
    expect(engine.routeProgramElement(second)).toBe(true)
    await engine.ensureReady()

    const context = FakeContext.instances[0]!
    expect(FakeContext.instances).toHaveLength(1)
    expect(context.mediaElements).toEqual([first, second])
    expect(context.resume).toHaveBeenCalledTimes(1)
    expect(context.mediaNodes[0]?.disconnect).toHaveBeenCalled()
    expect(engine.getSnapshot().mixer).toMatchObject({
      crossfader: 0,
      trims: { A: 0.5, B: 0.5 },
      eq: { A: { low: 0.5, mid: 0.5, high: 0.5 }, B: { low: 0.5, mid: 0.5, high: 0.5 } },
    })
  })

  it("propagates context-resume failure and never calls media playback itself", async () => {
    class FailingContext extends FakeContext {
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
    expect(FakeContext.instances).toHaveLength(0)
  })

  it("keeps both sources attached through handover and retires outgoing only after confirmation", async () => {
    vi.stubGlobal("AudioContext", FakeContext)
    const engine = new PlaybackEngine()
    const outgoing = document.createElement("audio")
    const incoming = document.createElement("audio")
    engine.routeProgramElement(outgoing, 1, "queue-1")
    const incomingDeck = engine.routeIncomingElement(incoming, 2, "queue-2")

    const result = await engine.handover({ incomingDeck: incomingDeck!, clientHandoverId: "h-1" })
    const context = FakeContext.instances[0]!
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
    vi.stubGlobal("AudioContext", FakeContext)
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

  it("projects ended and failed external media states", () => {
    vi.stubGlobal("AudioContext", FakeContext)
    const engine = new PlaybackEngine()
    const media = document.createElement("audio")
    engine.routeProgramElement(media)

    Object.defineProperty(media, "ended", { configurable: true, value: true })
    expect(engine.getSnapshot().decks.A.transport).toBe("ended")

    Object.defineProperty(media, "error", { configurable: true, value: { code: 3 } })
    expect(engine.getSnapshot().decks.A.transport).toBe("error")
  })

  it("upgrades a fully buffered eligible deck to Signalsmith and keeps native fallback otherwise", async () => {
    vi.stubGlobal("AudioContext", FakeContext)
    const stretchNode = Object.assign(new FakeNode(), {
      inputTime: 0,
      configure: vi.fn().mockResolvedValue(undefined),
      latency: vi.fn().mockResolvedValue(0.12),
      addBuffers: vi.fn().mockResolvedValue(2),
      dropBuffers: vi.fn().mockResolvedValue({ start: 0, end: 0 }),
      schedule: vi.fn().mockImplementation(async (change) => change),
      start: vi.fn().mockResolvedValue({}),
      stop: vi.fn().mockResolvedValue({}),
      setUpdateInterval: vi.fn().mockResolvedValue(undefined),
      port: { close: vi.fn() },
    }) as unknown as StretchNode
    const eligible = vi.fn().mockResolvedValue({ ready: true, reason: null })
    const engine = new PlaybackEngine(undefined, {
      stretch: { createNode: vi.fn().mockResolvedValue(stretchNode) },
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
})
