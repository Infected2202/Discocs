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
    routeProgramElement: vi.fn().mockReturnValue(true),
    routeIncomingElement: vi.fn().mockReturnValue("B"),
    ensureReady: vi.fn().mockResolvedValue({}),
    handover: vi.fn().mockResolvedValue({ outgoingDeck: "A", programDeck: "B", clientHandoverId: "h-1" }),
    confirmRetirement: vi.fn().mockResolvedValue(undefined),
    cancelIncoming: vi.fn(),
    destroy: vi.fn().mockResolvedValue(undefined),
  } as unknown as PlaybackEngine
}

describe("PlayerPlaybackFacade routing", () => {
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
