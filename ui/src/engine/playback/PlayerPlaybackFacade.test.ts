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

  emit(type: string) {
    for (const [event, listener] of this.addEventListener.mock.calls) {
      if (event === type) (listener as EventListener)(new Event(type))
    }
  }
}

function runtime() {
  const snapshot = {
    programDeck: "A" as const,
    decks: {
      A: { sourceKind: "media-element" as const, transport: "paused" as const, duration: 120, anchor: null },
      B: { sourceKind: "media-element" as const, transport: "paused" as const, duration: 120, anchor: null },
    },
    tempoSync: {
      auto: true,
      master: "clock" as const,
      clockBpm: 126,
      decks: {
        A: { enabled: false, phase: "off" as const, reason: null },
        B: { enabled: false, phase: "off" as const, reason: null },
      },
    },
  }
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
    toggleSync: vi.fn(async (deck: "A" | "B") => {
      snapshot.tempoSync.decks[deck].enabled = !snapshot.tempoSync.decks[deck].enabled
    }),
    setMasterGain: vi.fn(),
    upgradeDeckSource: vi.fn().mockResolvedValue({ upgraded: false, kind: "media-element", reason: null }),
    getSnapshot: vi.fn(() => snapshot),
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
    expect(engine.toggleSync).toHaveBeenCalledWith("B", "beat")
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

    vi.mocked(engine.isStretchDeck).mockReturnValue(true)
    finishUpgrade({ upgraded: true, kind: "signalsmith", reason: null })
    await activation
    await sync
    expect(engine.toggleSync).toHaveBeenCalledWith("A", "beat")
  })

  it("keeps SYNC armed and retries a failed follower upgrade before realigning it", async () => {
    vi.stubGlobal("Audio", function () { return new MockAudio() })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) }))
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:prepared-sync")
    const engine = runtime()
    const stretchDecks = new Set<string>()
    vi.mocked(engine.isStretchDeck).mockImplementation((deck) => stretchDecks.has(deck))
    vi.mocked(engine.upgradeDeckSource)
      .mockImplementationOnce(async () => {
        stretchDecks.add("A")
        return { upgraded: true, kind: "signalsmith", reason: null }
      })
      .mockResolvedValueOnce({ upgraded: false, kind: "media-element", reason: "worklet was not ready" })
      .mockImplementationOnce(async () => {
        stretchDecks.add("B")
        return { upgraded: true, kind: "signalsmith", reason: null }
      })
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/1", 1, "raw", false, "queue-1")
    await facade.activateDjMode()
    await facade.prepareDjDeck(2, "/audio/2", "raw", "queue-2")

    await facade.toggleDeckSync("B")

    expect(engine.toggleSync).toHaveBeenCalledWith("B", "beat")
    expect(engine.upgradeDeckSource).toHaveBeenLastCalledWith(
      "B",
      expect.objectContaining({ trackId: 2, queueItemId: "queue-2", blob: expect.any(Blob) }),
      { startAtSeconds: 0, autoplay: false },
    )
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
    await facade.activateDjMode()
    await facade.prepareDjDeck(2, "/audio/2", "raw", "queue-2")

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

  it("keeps ordinary playback off the Web Audio graph until DJ mode is activated", async () => {
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

    // Обычный режим: элемент НЕ заводится в граф и AudioContext не трогается —
    // именно чистый <audio> надёжно играет в фоне на мобиле.
    expect(engine.routeProgramElement).not.toHaveBeenCalled()
    expect(engine.ensureReady).not.toHaveBeenCalled()
    expect(audio[1]?.play).toHaveBeenCalledTimes(1)

    // Активация DJ одноразово заводит живой элемент в микшер.
    await facade.activateDjMode()
    expect(engine.ensureReady).toHaveBeenCalled()
    expect(engine.routeProgramElement).toHaveBeenCalledWith(audio[1], 7, null)
    expect(facade.djModeActive).toBe(true)
  })

  it("keeps the forced active Blob replacement off the graph in ordinary mode", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(new Blob(["complete audio"])),
    }))
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:active-ordinary")
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/7", 7, "raw", false, "queue-7")

    await facade.play()
    await vi.waitFor(() => expect(audio).toHaveLength(3))

    expect(audio.at(-1)?.src).toBe("blob:active-ordinary")
    expect(engine.routeProgramElement).not.toHaveBeenCalled()
  })

  it("routes the forced active Blob replacement when the DJ engine is active", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    let finishBlob!: (blob: Blob) => void
    const blob = new Promise<Blob>((resolve) => { finishBlob = resolve })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => blob }))
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:active-dj")
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/7", 7, "raw", false, "queue-7")
    await facade.play()
    await facade.activateDjMode()

    finishBlob(new Blob(["complete audio"]))
    await vi.waitFor(() => expect(audio).toHaveLength(3))

    expect(engine.routeProgramElement).toHaveBeenLastCalledWith(
      audio.at(-1), 7, "queue-7",
    )
  })

  it("reapplies the user's seek when a broken network seek resets before the Blob swap", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    let finishBlob!: (blob: Blob) => void
    const blob = new Promise<Blob>((resolve) => { finishBlob = resolve })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => blob }))
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:active-seek")
    const facade = new PlayerPlaybackFacade(runtime())
    facade.load("/audio/7", 7, "raw", false, "queue-7")
    const networkElement = audio.at(-1)!
    networkElement.duration = 200
    networkElement.paused = false
    await facade.play()

    facade.seek(0.6)
    expect(networkElement.currentTime).toBe(120)
    // A non-seekable upstream stream can acknowledge the assignment and then
    // restart from zero before the forced full-track Blob is ready.
    networkElement.currentTime = 0

    finishBlob(new Blob(["complete audio"]))
    await vi.waitFor(() => expect(audio).toHaveLength(3))
    const blobElement = audio.at(-1)!
    blobElement.duration = 200
    blobElement.emit("loadedmetadata")

    expect(blobElement.currentTime).toBe(120)
    expect(blobElement.play).toHaveBeenCalledOnce()
  })

  it("preserves the live network position after a successful seek is confirmed", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    let finishBlob!: (blob: Blob) => void
    const blob = new Promise<Blob>((resolve) => { finishBlob = resolve })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => blob }))
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:active-confirmed-seek")
    const facade = new PlayerPlaybackFacade(runtime())
    facade.load("/audio/7", 7)
    const networkElement = audio.at(-1)!
    networkElement.duration = 200
    networkElement.paused = false
    await facade.play()

    facade.seek(0.6)
    networkElement.emit("seeked")
    networkElement.currentTime = 127

    finishBlob(new Blob(["complete audio"]))
    await vi.waitFor(() => expect(audio).toHaveLength(3))
    const blobElement = audio.at(-1)!
    blobElement.duration = 200
    blobElement.emit("loadedmetadata")

    expect(blobElement.currentTime).toBe(127)
  })

  it("does not create a graph deck for prefetch while DJ mode is inactive", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) }))
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:prefetched-8")
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/7", 7)

    await facade.prefetch(8, "/audio/8", "raw", "queue-8")

    // Only the program element exists — no incoming deck routed into the graph.
    expect(engine.routeIncomingElement).not.toHaveBeenCalled()
    expect(facade.hasPrepared(8, "queue-8")).toBe(false)
    // The blob is still cached and consumable for the next ordinary <audio>.
    expect(facade.consumePrefetched(8, "raw")).toBe("blob:prefetched-8")
  })

  it("never routes an ordinary prefetch into the mixer graph, even while DJ mode is active", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) }))
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:prefetched-dj-active")
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/7", 7)
    await facade.activateDjMode()
    vi.mocked(engine.routeIncomingElement).mockClear()

    await facade.prefetch(8, "/audio/8", "raw", "queue-8")

    // prefetch() is fully graph-unaware by design (R5): it never routes an
    // incoming element into the mixer graph, regardless of DJ-mode state.
    // Only prepareDjDeck() may do that.
    expect(engine.routeIncomingElement).not.toHaveBeenCalled()
    expect(facade.hasPrepared(8, "queue-8")).toBe(false)
  })

  it("does not tear down a manually-prepared DJ deck when ordinary background prefetch runs afterward", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) }))
    vi.spyOn(URL, "createObjectURL")
      .mockReturnValueOnce("blob:dj-deck-armed")
      .mockReturnValue("blob:background-prefetch")
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/1", 1, "raw", false, "queue-1")
    await facade.activateDjMode()

    // DJ manually arms deck B with track 2.
    await facade.prepareDjDeck(2, "/audio/2", "raw", "queue-2")
    expect(facade.hasPrepared(2, "queue-2")).toBe(true)

    // Routine background caching of the ordinary next-queue track (an
    // unrelated track, 3) runs afterward — this must never touch the DJ deck.
    await facade.prefetch(3, "/audio/3", "raw", "queue-3")

    expect(facade.hasPrepared(2, "queue-2")).toBe(true)
    expect(engine.cancelIncoming).not.toHaveBeenCalled()
  })

  it("does not enter DJ mode when AudioContext resume fails", async () => {
    const media = new MockAudio()
    vi.stubGlobal("Audio", function () { return media })
    const engine = runtime()
    vi.mocked(engine.ensureReady).mockRejectedValueOnce(new Error("context suspended"))
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("/audio/8", 8)

    await expect(facade.activateDjMode()).rejects.toThrow("context suspended")
    expect(facade.djModeActive).toBe(false)
    // Обычное воспроизведение не зависит от графа и продолжает работать.
    await facade.play()
    expect(media.play).toHaveBeenCalled()
  })

  it("returns to a fresh unrouted element at the same position when DJ mode is deactivated", async () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)
    facade.load("blob:audio-9", 9, "raw", true)
    const program = audio.at(-1)!
    program.currentTime = 54
    program.paused = false

    await facade.activateDjMode()
    expect(facade.djModeActive).toBe(true)

    await facade.deactivateDjMode()

    expect(facade.djModeActive).toBe(false)
    expect(engine.destroy).toHaveBeenCalledTimes(1)
    // A brand-new <audio> replaced the routed one (createMediaElementSource is
    // irreversible), seeded with the same blob source.
    const replacement = audio.at(-1)!
    expect(replacement).not.toBe(program)
    expect(replacement.src).toBe("blob:audio-9")
    const resume = replacement.addEventListener.mock.calls
      .filter(([event]) => event === "loadedmetadata").at(-1)?.[1]
    replacement.duration = 200
    ;(resume as EventListener)(new Event("loadedmetadata"))
    expect(replacement.currentTime).toBe(54)
    expect(replacement.play).toHaveBeenCalled()
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
    expect(engine.routeProgramElement).toHaveBeenLastCalledWith(current, 9, "queue-9")
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
    await facade.activateDjMode()

    await facade.prepareDjDeck(2, "/audio/2", "raw", "queue-2")
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
