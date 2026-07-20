import { beforeEach, describe, expect, it, vi } from "vitest"
import { PlaybackEngine } from "./PlaybackEngine"

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
  addEventListener = vi.fn()
  removeEventListener = vi.fn()

  constructor() {
    FakeContext.instances.push(this)
  }
}

describe("PlaybackEngine Phase 1 routing", () => {
  beforeEach(() => {
    FakeContext.instances.length = 0
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
  })
})
