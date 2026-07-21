import { describe, expect, it, vi } from "vitest"
import type { PlaybackEngine } from "./PlaybackEngine"
import { PlayerPlaybackFacade } from "./PlayerPlaybackFacade"

class MockAudio {
  src = ""
  preload = ""
  volume = 1
  muted = false
  currentTime = 0
  duration = 120
  paused = true
  ended = false
  buffered = { length: 0, start: vi.fn(), end: vi.fn() }
  error: MediaError | null = null
  addEventListener = vi.fn()
  removeEventListener = vi.fn()
  load = vi.fn()
  pause = vi.fn()
  play = vi.fn().mockResolvedValue(undefined)
}

function runtime() {
  return {
    programDeck: "A",
    routeProgramElement: vi.fn().mockReturnValue(true),
    routeIncomingElement: vi.fn().mockReturnValue("B"),
    ensureReady: vi.fn().mockResolvedValue({}),
    handover: vi.fn().mockResolvedValue({ outgoingDeck: "A", programDeck: "B", clientHandoverId: "h-1" }),
    confirmRetirement: vi.fn().mockResolvedValue(undefined),
    cancelIncoming: vi.fn(),
    isStretchDeck: vi.fn().mockReturnValue(false),
    playDeck: vi.fn().mockResolvedValue(undefined),
    pauseDeck: vi.fn().mockResolvedValue(undefined),
    seekDeck: vi.fn().mockResolvedValue(undefined),
    setTempo: vi.fn().mockResolvedValue(undefined),
    setAutoMaster: vi.fn().mockResolvedValue(undefined),
    setClockMaster: vi.fn().mockResolvedValue(undefined),
    setTempoMaster: vi.fn().mockResolvedValue(undefined),
    setClockTempo: vi.fn().mockResolvedValue(undefined),
    toggleSync: vi.fn().mockResolvedValue(undefined),
    setMasterGain: vi.fn(),
    upgradeDeckSource: vi.fn().mockResolvedValue({ upgraded: false, kind: "media-element", reason: null }),
    getSnapshot: vi.fn().mockReturnValue({
      programDeck: "A",
      decks: {
        A: { sourceKind: "media-element", transport: "paused", duration: 120, anchor: null },
        B: { sourceKind: "media-element", transport: "paused", duration: 120, anchor: null },
      },
    }),
    getMeterLevels: vi.fn().mockReturnValue({ A: 0.2, B: 0.1, master: 0.25 }),
    destroy: vi.fn().mockResolvedValue(undefined),
    subscribe: vi.fn(() => () => undefined),
  } as unknown as PlaybackEngine
}

describe("PlayerPlaybackFacade routing", () => {
  it("forwards master-clock and deck-sync ownership commands", async () => {
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)

    await facade.setAutoTempoMaster()
    await facade.setClockTempoMaster()
    await facade.setDeckTempoMaster("B")
    await facade.setMasterClockTempo(128.5)
    await facade.toggleDeckSync("B")

    expect(engine.setAutoMaster).toHaveBeenCalledOnce()
    expect(engine.setClockMaster).toHaveBeenCalledOnce()
    expect(engine.setTempoMaster).toHaveBeenCalledWith("B")
    expect(engine.setClockTempo).toHaveBeenCalledWith(128.5)
    expect(engine.toggleSync).toHaveBeenCalledWith("B")
  })

  it("waits for an in-progress full-track deck upgrade before engaging SYNC", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    let finishUpgrade!: (result: { upgraded: true; kind: "signalsmith"; reason: null }) => void
    const upgrade = new Promise<{ upgraded: true; kind: "signalsmith"; reason: null }>((resolve) => {
      finishUpgrade = resolve
    })
    const engine = runtime()
    vi.mocked(engine.upgradeDeckSource).mockReturnValueOnce(upgrade)
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/7", 7, "raw", true)

    const activation = facade.activateDjMode()
    const sync = facade.toggleDeckSync("A")
    expect(engine.toggleSync).not.toHaveBeenCalled()

    finishUpgrade({ upgraded: true, kind: "signalsmith", reason: null })
    await activation
    await sync
    expect(engine.toggleSync).toHaveBeenCalledWith("A")
  })

  it("defers a fractional seek until replacement media metadata is ready", () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/1", 1)
    const element = audio.at(-1)!
    element.duration = Number.NaN

    facade.seek(0.4)
    expect(element.currentTime).toBe(0)
    const loadedMetadata = element.addEventListener.mock.calls
      .filter(([event]) => event === "loadedmetadata")
      .at(-1)?.[1]
    expect(loadedMetadata).toBeTypeOf("function")

    element.duration = 200
    ;(loadedMetadata as EventListener)(new Event("loadedmetadata"))
    expect(element.currentTime).toBe(80)
  })

  it("seeks the requested physical deck and clamps to its media duration", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) }))
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:prepared-seek")
    const facade = new PlayerPlaybackFacade(runtime())
    facade.load("/audio/1", 1)
    await facade.prefetch(2, "/audio/2", "raw", "queue-2")

    facade.seekDeckToSeconds("B", 999)

    expect(audio[2]?.currentTime).toBe(120)
    expect(audio[1]?.currentTime).toBe(0)
    expect(facade.getDeckCurrentTime("B")).toBe(120)
    expect(facade.getDeckCurrentTime("A")).toBe(0)
  })

  it("forwards physical deck and mixer controls to the shared runtime", () => {
    const engine = runtime()
    Object.assign(engine, {
      setTrim: vi.fn(),
      setEq: vi.fn(),
      setFilter: vi.fn(),
      setChannelFader: vi.fn(),
      setCrossfader: vi.fn(),
      setMasterGain: vi.fn(),
    })
    const facade = new PlayerPlaybackFacade(engine)

    facade.setDeckTrim("B", 0.6)
    facade.setDeckEq("A", "mid", 0.7)
    facade.setDeckFilter("B", -0.3)
    facade.setDeckChannelFader("A", 0.4)
    facade.setCrossfader(0.2)
    facade.setMasterGain(0.9)

    expect(engine.setTrim).toHaveBeenCalledWith("B", 0.6)
    expect(engine.setEq).toHaveBeenCalledWith("A", "mid", 0.7)
    expect(engine.setFilter).toHaveBeenCalledWith("B", -0.3)
    expect(engine.setChannelFader).toHaveBeenCalledWith("A", 0.4)
    expect(engine.setCrossfader).toHaveBeenCalledWith(0.2)
    expect(engine.setMasterGain).toHaveBeenCalledWith(0.9)
    expect(facade.getMixerMeters()).toEqual({ A: 0.2, B: 0.1, master: 0.25 })
  })

  it("routes every loaded element and resumes Web Audio before media playback", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)

    facade.load("blob:audio-7", 7, "raw", true)
    await facade.play()

    expect(engine.routeProgramElement).toHaveBeenCalledWith(audio[1], 7, null)
    expect(engine.ensureReady).toHaveBeenCalledTimes(1)
    expect(audio[1]?.play).toHaveBeenCalledTimes(1)
    expect(vi.mocked(engine.ensureReady).mock.invocationCallOrder[0]).toBeLessThan(
      audio[1]!.play.mock.invocationCallOrder[0]!,
    )
  })

  it("does not start media when AudioContext resume fails", async () => {
    const media = new MockAudio()
    vi.stubGlobal("Audio", function () { return media })
    const engine = runtime()
    vi.mocked(engine.ensureReady).mockRejectedValueOnce(new Error("context suspended"))
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/8", 8)

    await expect(facade.play()).rejects.toThrow("context suspended")
    expect(media.play).not.toHaveBeenCalled()
  })

  it("upgrades the current track at its native playhead when DJ mode opens", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    const engine = runtime()
    vi.mocked(engine.upgradeDeckSource).mockResolvedValueOnce({
      upgraded: true,
      kind: "signalsmith",
      reason: null,
    })
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/9", 9, "raw", false, "queue-9")
    const current = audio.at(-1)!
    current.currentTime = 37
    current.paused = false

    await facade.activateDjMode()

    expect(engine.setMasterGain).toHaveBeenCalledWith(1)
    expect(engine.upgradeDeckSource).toHaveBeenCalledWith(
      "A",
      { url: "/audio/9", trackId: 9, queueItemId: "queue-9", blob: undefined },
      { startAtSeconds: 37, autoplay: true },
    )
    expect(current.pause).toHaveBeenCalled()
  })

  it("promotes the prepared element without load and delays outgoing cleanup until confirmation", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) }))
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:prepared")
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined)
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/1", 1)

    await facade.prefetch(2, "/audio/2", "raw", "queue-2")
    expect(facade.hasPrepared(2, "queue-2")).toBe(true)
    const result = await facade.handoverPrepared("h-1")

    expect(result).toMatchObject({ trackId: 2, queueItemId: "queue-2", programDeck: "B" })
    expect(audio[1]?.src).toBe("/audio/1")
    expect(audio[2]?.play).toHaveBeenCalledTimes(1)
    expect(engine.handover).toHaveBeenCalledTimes(1)

    await facade.confirmHandover()
    expect(audio[1]?.src).toBe("")
    expect(engine.confirmRetirement).toHaveBeenCalledWith("A")
  })
})
