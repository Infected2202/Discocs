import { describe, expect, it, vi } from "vitest"
import type { PlaybackEngine } from "./PlaybackEngine"
import { PlayerPlaybackFacade, SOURCE_STALL_MS } from "./PlayerPlaybackFacade"
import {
  installFakeAudio,
  MEDIA_HAVE_ENOUGH_DATA,
  MEDIA_NETWORK_IDLE,
  type FakeMediaElement,
} from "./testing/webAudioFakes"

function stubCallbacks() {
  return {
    onTimeUpdate: vi.fn(),
    onPlaybackStateChange: vi.fn(),
    onBufferUpdate: vi.fn(),
    onBufferingSettled: vi.fn(),
    onSeekBufferingChange: vi.fn(),
    onNextTrackBufferingChange: vi.fn(),
    onEnded: vi.fn(),
    onError: vi.fn(),
  }
}

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
    beginTempoNudge: vi.fn(),
    endTempoNudge: vi.fn(),
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

  it("threads an explicit TempoSync mode through to the engine", async () => {
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)

    await facade.toggleDeckSync("A", "tempo")

    expect(engine.toggleSync).toHaveBeenCalledWith("A", "tempo")
  })

  it("forwards tempo-nudge press/release straight through to the engine", () => {
    const engine = runtime()
    const facade = new PlayerPlaybackFacade(engine)

    facade.beginTempoNudge("B", "up")
    facade.endTempoNudge("B")

    expect(engine.beginTempoNudge).toHaveBeenCalledWith("B", "up")
    expect(engine.endTempoNudge).toHaveBeenCalledWith("B")
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
    // A blob: source (already local — e.g. a consumed prefetch) with no
    // known duration yet: the fraction can only be resolved on metadata.
    facade.load("blob:audio-1", 1)
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

    // audio[0] — the ordinary program element (now routed), audio[1] — deck B.
    expect(audio[1]?.currentTime).toBe(120)
    expect(audio[0]?.currentTime).toBe(0)
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
    expect(audio[0]?.play).toHaveBeenCalledTimes(1)

    // Активация DJ одноразово заводит живой элемент в микшер.
    await facade.activateDjMode()
    expect(engine.ensureReady).toHaveBeenCalled()
    expect(engine.routeProgramElement).toHaveBeenCalledWith(audio[0], 7, null)
    expect(facade.djModeActive).toBe(true)
  })

  it("writes currentTime directly when the seek target is already covered by native buffering", () => {
    const audio: MockAudio[] = []
    vi.stubGlobal("Audio", function () {
      const instance = new MockAudio()
      audio.push(instance)
      return instance
    })
    const fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
    const callbacks = stubCallbacks()
    const facade = new PlayerPlaybackFacade(runtime())
    facade.init(callbacks)
    facade.load("/audio/7", 7, "raw", false, "queue-7")
    const networkElement = audio.at(-1)!
    networkElement.duration = 200
    // Native progressive download has already buffered [0, 150] — the 0.6
    // target (120s) falls well inside it, so no full-file wait is needed.
    networkElement.buffered = {
      length: 1,
      start: vi.fn().mockReturnValue(0),
      end: vi.fn().mockReturnValue(150),
    }

    facade.seek(0.6)

    expect(networkElement.currentTime).toBe(120)
    expect(networkElement.pause).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(callbacks.onSeekBufferingChange).not.toHaveBeenCalled()
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

  it("reports next-track buffering transitions through onNextTrackBufferingChange", async () => {
    vi.stubGlobal("Audio", function () { return new MockAudio() })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) }))
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:next-track-ready")
    const callbacks = stubCallbacks()
    const facade = new PlayerPlaybackFacade(runtime())
    facade.init(callbacks)
    facade.load("/audio/7", 7)

    await facade.prefetch(8, "/audio/8", "raw", "queue-8")

    expect(callbacks.onNextTrackBufferingChange).toHaveBeenNthCalledWith(1, {
      trackId: 8, queueItemId: "queue-8", ready: false,
    })
    expect(callbacks.onNextTrackBufferingChange).toHaveBeenLastCalledWith({
      trackId: 8, queueItemId: "queue-8", ready: true,
    })

    facade.consumePrefetched(8, "raw")
    expect(callbacks.onNextTrackBufferingChange).toHaveBeenLastCalledWith(null)
  })

  it("clears the next-track buffering indicator when a prefetch is cancelled or dropped", async () => {
    vi.stubGlobal("Audio", function () { return new MockAudio() })
    let resolveFetch!: (value: { ok: boolean; blob: () => Promise<Blob> }) => void
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise((resolve) => { resolveFetch = resolve })))
    const callbacks = stubCallbacks()
    const facade = new PlayerPlaybackFacade(runtime())
    facade.init(callbacks)
    facade.load("/audio/7", 7)

    void facade.prefetch(8, "/audio/8", "raw", "queue-8")
    expect(callbacks.onNextTrackBufferingChange).toHaveBeenLastCalledWith({
      trackId: 8, queueItemId: "queue-8", ready: false,
    })

    facade.cancelPrefetch()
    expect(callbacks.onNextTrackBufferingChange).toHaveBeenLastCalledWith(null)
    resolveFetch({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) })
  })

  it("retries a failed prefetch once, then succeeds and reports ready", async () => {
    vi.stubGlobal("Audio", function () { return new MockAudio() })
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error("network hiccup"))
      .mockResolvedValueOnce({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) })
    vi.stubGlobal("fetch", fetchMock)
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:prefetch-retry-success")
    const callbacks = stubCallbacks()
    const facade = new PlayerPlaybackFacade(runtime())
    facade.init(callbacks)
    facade.load("/audio/7", 7)

    await facade.prefetch(8, "/audio/8", "raw", "queue-8")

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(callbacks.onNextTrackBufferingChange).toHaveBeenLastCalledWith({
      trackId: 8, queueItemId: "queue-8", ready: true,
    })
  })

  it("rejects after one retry when prefetch keeps failing, without an infinite loop", async () => {
    vi.stubGlobal("Audio", function () { return new MockAudio() })
    const fetchMock = vi.fn().mockRejectedValue(new Error("still failing"))
    vi.stubGlobal("fetch", fetchMock)
    const facade = new PlayerPlaybackFacade(runtime())
    facade.load("/audio/7", 7)

    await expect(facade.prefetch(8, "/audio/8", "raw", "queue-8")).rejects.toThrow("still failing")
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("relabels an already-prefetched track instead of re-fetching when queue_item_id changes", async () => {
    vi.stubGlobal("Audio", function () { return new MockAudio() })
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) })
    vi.stubGlobal("fetch", fetchMock)
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:relabel-8")
    const callbacks = stubCallbacks()
    const facade = new PlayerPlaybackFacade(runtime())
    facade.init(callbacks)
    facade.load("/audio/7", 7)

    await facade.prefetch(8, "/audio/8", "raw", "queue-8a")
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // A queue resync (e.g. the background PATCH sync after an optimistic
    // jump) can hand the same still-upcoming track a fresh queue_item_id.
    // That must not discard the Blob we already have and refetch it.
    await facade.prefetch(8, "/audio/8", "raw", "queue-8b")

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(callbacks.onNextTrackBufferingChange).toHaveBeenLastCalledWith({
      trackId: 8, queueItemId: "queue-8b", ready: true,
    })
    expect(facade.consumePrefetched(8, "raw")).toBe("blob:relabel-8")
  })

  it("does not restart an in-flight prefetch when relabelled with a different queue_item_id, and resolves under the latest label", async () => {
    vi.stubGlobal("Audio", function () { return new MockAudio() })
    let resolveFetch!: (value: { ok: boolean; blob: () => Promise<Blob> }) => void
    const fetchMock = vi.fn().mockReturnValue(new Promise((resolve) => { resolveFetch = resolve }))
    vi.stubGlobal("fetch", fetchMock)
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:relabel-inflight-8")
    const callbacks = stubCallbacks()
    const facade = new PlayerPlaybackFacade(runtime())
    facade.init(callbacks)
    facade.load("/audio/7", 7)

    const first = facade.prefetch(8, "/audio/8", "raw", "queue-8a")
    // Relabel while the fetch is still in flight — must not abort/restart it.
    const second = facade.prefetch(8, "/audio/8", "raw", "queue-8b")

    resolveFetch({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) })
    await Promise.all([first, second])

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(callbacks.onNextTrackBufferingChange).toHaveBeenLastCalledWith({
      trackId: 8, queueItemId: "queue-8b", ready: true,
    })
    expect(facade.consumePrefetched(8, "raw")).toBe("blob:relabel-inflight-8")
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
    expect(audio[0]?.src).toBe("/audio/1")
    expect(audio[1]?.play).toHaveBeenCalledTimes(1)
    expect(engine.handover).toHaveBeenCalledTimes(1)

    await facade.confirmHandover()
    expect(audio[0]?.src).toBe("")
    expect(engine.confirmRetirement).toHaveBeenCalledWith("A")
  })
})

const RAW_URL = "/api/v1/tracks/7/audio?profile=raw"
const MP3_URL = "/api/v1/tracks/7/audio?profile=mp3-192"

function streamingFacade() {
  const audio = installFakeAudio()
  const fetchMock = vi.fn()
  vi.stubGlobal("fetch", fetchMock)
  const callbacks = stubCallbacks()
  const engine = runtime()
  const facade = new PlayerPlaybackFacade(engine)
  facade.init(callbacks)
  const current = (): FakeMediaElement => audio.at(-1)!
  return { audio, fetchMock, callbacks, engine, facade, current }
}

// Android Chrome ties the media notification (and with it the app's background
// network) to the page's media session: a new <audio> per track with the old
// one paused and emptied dropped it on every track change. Ordinary playback
// keeps one element and only swaps its source, like other web players.
describe("PlayerPlaybackFacade single ordinary element", () => {
  it("plays every track on the same element without pausing or emptying it", async () => {
    const { audio, facade, current } = streamingFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 200)
    await facade.play()
    const element = current()

    facade.load("blob:next-8", 8, "raw", true, "queue-8", 180)
    await facade.play()

    expect(audio).toHaveLength(1)
    expect(current()).toBe(element)
    expect(element.src).toBe("blob:next-8")
    expect(element.pause).not.toHaveBeenCalled()
    expect(element.play).toHaveBeenCalledTimes(2)
  })

  it("ignores a stale pause of the previous source once the next track is playing", async () => {
    const { callbacks, facade, current } = streamingFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 200)
    await facade.play()
    facade.load("blob:next-8", 8, "raw", true, "queue-8", 180)
    await facade.play()

    current().emit("pause")

    expect(callbacks.onPlaybackStateChange).not.toHaveBeenCalledWith("paused")
  })

  it("still gives each track its own element in DJ mode, where an element cannot leave the graph", async () => {
    const { audio, engine, facade } = streamingFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 200)
    await facade.activateDjMode()
    const routed = audio.at(-1)!

    facade.load("/audio/8", 8, "raw", false, "queue-8", 180)

    expect(audio.at(-1)).not.toBe(routed)
    expect(routed.src).toBe("")
    expect(engine.routeProgramElement).toHaveBeenLastCalledWith(audio.at(-1), 8, "queue-8")
  })
})

describe("PlayerPlaybackFacade progressive streaming", () => {
  it.each([
    ["raw", RAW_URL],
    ["mp3-192", MP3_URL],
  ])("plays the current %s track as a plain stream without downloading it a second time", async (profile, url) => {
    const { audio, fetchMock, facade, current } = streamingFacade()
    facade.load(url, 7, profile, false, "queue-7", 200)

    await facade.play()
    current().loadMetadata(200)
    current().setBuffered([[0, 30]])
    current().emit("progress")

    expect(fetchMock).not.toHaveBeenCalled()
    // The one ordinary element — the current track is never swapped to a Blob.
    expect(audio).toHaveLength(1)
    expect(current().src).toBe(url)
    expect(current().play).toHaveBeenCalledOnce()
  })

  it("seeks a raw (Range-capable) stream immediately, even outside the buffered part", async () => {
    const { fetchMock, callbacks, facade, current } = streamingFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 200)
    await facade.play()
    const el = current()
    el.loadMetadata(200)
    el.setBuffered([[0, 30]])

    facade.seek(0.6)

    expect(el.currentTime).toBe(120)
    expect(el.src).toBe(RAW_URL)
    expect(el.pause).not.toHaveBeenCalled()
    expect(el.load).toHaveBeenCalledOnce()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(callbacks.onSeekBufferingChange).not.toHaveBeenCalled()
    expect(facade.currentTime).toBe(120)
  })

  it("reloads a transcoded stream with t for a seek outside what it can seek natively", async () => {
    const { fetchMock, callbacks, facade, current } = streamingFacade()
    facade.load(MP3_URL, 7, "mp3-192", false, "queue-7", 200)
    await facade.play()
    const el = current()
    // A transcode answers Range with the whole stream: Chrome keeps it
    // buffered but only [0, 0] seekable.
    el.loadMetadata(204.8)
    el.setBuffered([[0, 30]])
    el.setSeekable([[0, 0]])

    facade.seek(0.6)

    expect(el.src).toBe(`${MP3_URL}&t=120`)
    expect(el.load).toHaveBeenCalledTimes(2)
    expect(el.play).toHaveBeenCalledTimes(2)
    expect(el.pause).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(callbacks.onSeekBufferingChange).toHaveBeenLastCalledWith(true)
    expect(facade.currentTime).toBe(120)

    // The element's timeline starts at the offset; duration is the track's.
    el.currentTime = 5
    el.emit("timeupdate")
    expect(callbacks.onTimeUpdate).toHaveBeenLastCalledWith(125, 200)
    el.loadMetadata(80)
    el.setBuffered([[0, 20]])
    el.emit("progress")
    expect(callbacks.onBufferUpdate).toHaveBeenLastCalledWith([{ start: 0.6, end: 0.7 }])
    expect(facade.duration).toBe(200)

    el.emit("playing")
    expect(callbacks.onSeekBufferingChange).toHaveBeenLastCalledWith(false)
  })

  it("seeks natively inside the buffered, seekable part of a transcoded offset stream", async () => {
    const { facade, current } = streamingFacade()
    facade.load(MP3_URL, 7, "mp3-192", false, "queue-7", 200)
    await facade.play()
    const el = current()
    el.loadMetadata(200)
    el.setSeekable([[0, 0]])
    facade.seek(0.6)
    expect(el.src).toBe(`${MP3_URL}&t=120`)
    el.emit("playing")
    // Chunked offset stream: infinite duration, so the browser reports it seekable.
    el.loadMetadata(Number.POSITIVE_INFINITY)
    el.setBuffered([[0, 30]])
    el.setSeekable([[0, Number.POSITIVE_INFINITY]])

    facade.seek(0.65)

    expect(el.src).toBe(`${MP3_URL}&t=120`)
    expect(el.currentTime).toBe(10)
    expect(facade.currentTime).toBe(130)

    // Backwards past the offset is not in this stream at all: new offset.
    facade.seek(0.1)
    expect(el.src).toBe(`${MP3_URL}&t=20`)
    expect(facade.currentTime).toBe(20)
  })

  it("does not resume a transcoded seek reload the user paused while it was loading", async () => {
    const { callbacks, facade, current } = streamingFacade()
    facade.load(MP3_URL, 7, "mp3-192", false, "queue-7", 200)
    await facade.play()
    const el = current()
    el.loadMetadata(200)

    facade.seek(0.5)
    // The reload's own interruption is not reported as a user pause.
    el.emit("pause")
    expect(callbacks.onPlaybackStateChange).not.toHaveBeenCalledWith("paused")

    facade.pause()
    el.emit("pause")
    expect(callbacks.onPlaybackStateChange).toHaveBeenLastCalledWith("paused")
    el.emit("canplay")
    expect(callbacks.onSeekBufferingChange).toHaveBeenLastCalledWith(false)
    expect(el.play).toHaveBeenCalledTimes(2)
  })

  it("starts a transcoded track at a position with t and no seek indicator", () => {
    const { callbacks, facade, current } = streamingFacade()

    facade.load(MP3_URL, 7, "mp3-192", false, "queue-7", 200, 95.6)

    expect(current().src).toBe(`${MP3_URL}&t=95`)
    expect(facade.currentTime).toBe(95)
    expect(callbacks.onSeekBufferingChange).not.toHaveBeenCalled()
  })

  it("starts a raw track at a position by seeking natively once metadata is known", () => {
    const { facade, current } = streamingFacade()

    facade.load(RAW_URL, 7, "raw", false, "queue-7", 200, 42)
    const el = current()
    expect(el.src).toBe(RAW_URL)
    expect(el.currentTime).toBe(0)

    el.loadMetadata(200)
    expect(el.currentTime).toBe(42)
    expect(facade.currentTime).toBe(42)
  })

  it("starts a consumed Blob at a position natively", () => {
    const { facade, current } = streamingFacade()

    facade.load("blob:track-7", 7, "mp3-192", true, "queue-7", 200, 42)
    const el = current()
    el.loadMetadata(200)

    expect(el.src).toBe("blob:track-7")
    expect(el.currentTime).toBe(42)
  })

  it("resumeAtSeconds on a transcode restores the position through t", () => {
    const { callbacks, facade, current } = streamingFacade()
    facade.load(MP3_URL, 7, "mp3-192", false, "queue-7", 200)

    facade.resumeAtSeconds(61.2)

    expect(current().src).toBe(`${MP3_URL}&t=61`)
    expect(callbacks.onSeekBufferingChange).not.toHaveBeenCalled()
  })

  it("falls back to the plain source and a native seek when the offset stream fails", async () => {
    const { callbacks, facade, current } = streamingFacade()
    facade.load(MP3_URL, 7, "mp3-192", false, "queue-7", 200, 95)
    const el = current()
    let rejectPlay!: (error: Error) => void
    el.play.mockImplementationOnce(() => new Promise<void>((_resolve, reject) => { rejectPlay = reject }))
    const playing = facade.play()
    await vi.waitFor(() => expect(el.play).toHaveBeenCalledOnce())

    // e.g. the server answers 400 for `t` on a track served from a local file.
    el.emit("error")
    rejectPlay(Object.assign(new Error("no supported sources"), { name: "NotSupportedError" }))
    await expect(playing).resolves.toBeUndefined()

    expect(el.src).toBe(MP3_URL)
    expect(callbacks.onError).not.toHaveBeenCalled()
    el.loadMetadata(200)
    expect(el.currentTime).toBe(95)
    expect(facade.currentTime).toBe(95)
    expect(el.play).toHaveBeenCalledTimes(2)
  })

  it("hands the DJ upgrade the base URL and the absolute position of an offset stream", async () => {
    const { engine, facade, current } = streamingFacade()
    facade.load(MP3_URL, 7, "mp3-192", false, "queue-7", 200, 120)
    const el = current()
    el.currentTime = 4
    el.paused = false

    await facade.activateDjMode()

    expect(engine.upgradeDeckSource).toHaveBeenLastCalledWith(
      "A",
      expect.objectContaining({ url: MP3_URL, trackId: 7 }),
      { startAtSeconds: 124, autoplay: true },
    )
  })
})

describe("PlayerPlaybackFacade buffering settled", () => {
  it("settles once the browser idles its download with a minute of audio ahead, without a full download", async () => {
    const { fetchMock, callbacks, facade, current } = streamingFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)
    await facade.play()
    const el = current()
    el.loadMetadata(300)
    el.currentTime = 10
    el.setBuffered([[0, 100]])
    el.emit("progress")
    expect(callbacks.onBufferingSettled).not.toHaveBeenCalled()

    el.networkState = MEDIA_NETWORK_IDLE
    el.readyState = MEDIA_HAVE_ENOUGH_DATA
    el.emit("suspend")

    expect(callbacks.onBufferingSettled).toHaveBeenCalledOnce()
    expect(callbacks.onBufferingSettled).toHaveBeenCalledWith(7, "raw")
    expect(fetchMock).not.toHaveBeenCalled()
    // Not faked as fully buffered: the bar keeps showing real ranges.
    expect(callbacks.onBufferUpdate).not.toHaveBeenCalledWith([{ start: 0, end: 1 }])
  })

  it("does not settle on a suspend with too little audio buffered ahead", () => {
    const { callbacks, facade, current } = streamingFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)
    const el = current()
    el.loadMetadata(300)
    el.currentTime = 10
    el.setBuffered([[0, 40]])
    el.networkState = MEDIA_NETWORK_IDLE
    el.readyState = MEDIA_HAVE_ENOUGH_DATA

    el.emit("suspend")
    el.emit("progress")

    expect(callbacks.onBufferingSettled).not.toHaveBeenCalled()
  })

  it("settles via the safety net once the buffered end passes 80% of the track", () => {
    const { callbacks, facade, current } = streamingFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)
    const el = current()
    el.loadMetadata(300)
    el.setBuffered([[0, 239]])
    el.emit("progress")
    expect(callbacks.onBufferingSettled).not.toHaveBeenCalled()

    el.setBuffered([[0, 241]])
    el.emit("progress")
    el.emit("progress")

    expect(callbacks.onBufferingSettled).toHaveBeenCalledOnce()
  })

  it("settles via the safety net when less than 45 s remain to play", () => {
    const { callbacks, facade, current } = streamingFacade()
    // Short track: 44 s left while the buffered end is still below 80%.
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 200)
    const el = current()
    el.loadMetadata(200)
    el.currentTime = 150
    el.setBuffered([[148, 152]])
    el.emit("timeupdate")
    expect(callbacks.onBufferingSettled).not.toHaveBeenCalled()

    el.currentTime = 156
    el.setBuffered([[148, 158]])
    el.emit("timeupdate")

    expect(callbacks.onBufferingSettled).toHaveBeenCalledWith(7, "raw")
  })

  it("measures an offset stream on the track's timeline", async () => {
    const { callbacks, facade, current } = streamingFacade()
    facade.load(MP3_URL, 7, "mp3-192", false, "queue-7", 300)
    await facade.play()
    const el = current()
    el.loadMetadata(300)
    el.setBuffered([[0, 30]])
    el.setSeekable([[0, 0]])
    el.emit("progress")

    facade.seek(0.5)
    expect(el.src).toBe(`${MP3_URL}&t=150`)
    // 91 s into a stream starting at 150 s = 241 s of a 300 s track (>80%);
    // read without the offset it would be a mere 30%.
    el.setBuffered([[0, 91]])
    el.emit("progress")

    expect(callbacks.onBufferingSettled).toHaveBeenCalledWith(7, "mp3-192")
  })

  it("settles a fully local Blob immediately", () => {
    const { callbacks, facade } = streamingFacade()

    facade.load("blob:track-7", 7, "raw", true, "queue-7", 300)

    expect(callbacks.onBufferingSettled).toHaveBeenCalledWith(7, "raw")
    expect(callbacks.onBufferUpdate).toHaveBeenLastCalledWith([{ start: 0, end: 1 }])
  })

  it("requests the next-track prefetch at low fetch priority", async () => {
    const { fetchMock, facade } = streamingFacade()
    fetchMock.mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["audio"])) })
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:next-low")
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)

    await facade.prefetch(8, "/audio/8", "raw", "queue-8")

    expect(fetchMock).toHaveBeenCalledWith("/audio/8", expect.objectContaining({ priority: "low" }))
  })
})

// The user's prefetch_tracks setting above 1: tracks after the next one are
// kept downloaded too, so playback outlasts a background network cut.
describe("PlayerPlaybackFacade tracks prefetched ahead", () => {
  function aheadFacade() {
    const env = streamingFacade()
    env.fetchMock.mockImplementation((url: string) => Promise.resolve({
      ok: true,
      blob: () => Promise.resolve(new Blob([url])),
    }))
    let objectUrls = 0
    vi.spyOn(URL, "createObjectURL").mockImplementation(() => `blob:ahead-${++objectUrls}`)
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined)
    return { ...env, revoke }
  }
  const target = (trackId: number) => ({ trackId, url: `/audio/${trackId}`, profileKey: "raw" })

  it("downloads the tracks after the next one at low priority and promotes one to next without a refetch", async () => {
    const { fetchMock, callbacks, facade } = aheadFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)
    await facade.prefetch(8, "/audio/8", "raw", "queue-8")

    await facade.prefetchAhead([target(9), target(10)])

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/audio/8", "/audio/9", "/audio/10"])
    expect(fetchMock).toHaveBeenLastCalledWith("/audio/10", expect.objectContaining({ priority: "low" }))

    facade.load(facade.consumePrefetched(8, "raw")!, 8, "raw", true, "queue-8", 300)
    await facade.prefetch(9, "/audio/9", "raw", "queue-9")

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(callbacks.onNextTrackBufferingChange).toHaveBeenLastCalledWith({ trackId: 9, queueItemId: "queue-9", ready: true })
    expect(facade.consumePrefetched(9, "raw")).toBe("blob:ahead-2")
  })

  it("plays a pooled track directly when the queue skips straight to it", async () => {
    const { fetchMock, facade } = aheadFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)
    await facade.prefetch(8, "/audio/8", "raw", "queue-8")
    await facade.prefetchAhead([target(9)])

    expect(facade.consumePrefetched(9, "raw")).toBe("blob:ahead-2")
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("drops pooled tracks that are no longer wanted", async () => {
    const { facade, revoke } = aheadFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)
    await facade.prefetch(8, "/audio/8", "raw", "queue-8")
    await facade.prefetchAhead([target(9), target(10)])

    await facade.prefetchAhead([target(10)])

    expect(revoke).toHaveBeenCalledWith("blob:ahead-2")
    expect(revoke).not.toHaveBeenCalledWith("blob:ahead-3")
    expect(facade.consumePrefetched(9, "raw")).toBeNull()
  })

  it("does not fetch ahead while the next track itself is still downloading", async () => {
    const { fetchMock, facade } = aheadFacade()
    fetchMock.mockImplementationOnce(() => new Promise(() => undefined))
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)
    void facade.prefetch(8, "/audio/8", "raw", "queue-8")

    await facade.prefetchAhead([target(9)])

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/audio/8"])
  })

  it("joins a running download for the same targets instead of restarting it", async () => {
    const { fetchMock, facade } = aheadFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)
    await facade.prefetch(8, "/audio/8", "raw", "queue-8")

    await Promise.all([facade.prefetchAhead([target(9)]), facade.prefetchAhead([target(9)])])

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/audio/8", "/audio/9"])
  })
})

// A request lost while the phone's network slept in the background leaves the
// element without data forever (or errored); play() on it cannot revive it.
describe("PlayerPlaybackFacade dead source", () => {
  it("reports a source that got no data for SOURCE_STALL_MS as failed, not a fresh one", () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000_000)
    const { facade } = streamingFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)

    now.mockReturnValue(1_000_000 + SOURCE_STALL_MS - 1)
    expect(facade.sourceFailed).toBe(false)
    now.mockReturnValue(1_000_000 + SOURCE_STALL_MS)
    expect(facade.sourceFailed).toBe(true)
    now.mockRestore()
  })

  it("does not report a slow source that already has data", () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000_000)
    const { facade, current } = streamingFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)
    current().loadMetadata(300)

    now.mockReturnValue(1_000_000 + 10 * SOURCE_STALL_MS)
    expect(facade.sourceFailed).toBe(false)
    now.mockRestore()
  })

  it("reports a media error at once", () => {
    const { facade, current } = streamingFacade()
    facade.load(RAW_URL, 7, "raw", false, "queue-7", 300)
    current().loadMetadata(300)

    current().error = { code: 2, message: "net::ERR_INTERNET_DISCONNECTED" } as MediaError

    expect(facade.sourceFailed).toBe(true)
  })

  it("reports nothing when no track is loaded", () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000_000 + 10 * SOURCE_STALL_MS)
    const { facade } = streamingFacade()

    expect(facade.sourceFailed).toBe(false)
    now.mockRestore()
  })
})
