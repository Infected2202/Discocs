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
    ensureReady: vi.fn().mockResolvedValue({}),
    destroy: vi.fn().mockResolvedValue(undefined),
  } as unknown as PlaybackEngine
}

describe("PlayerPlaybackFacade routing", () => {
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

    expect(engine.routeProgramElement).toHaveBeenCalledWith(audio[1])
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
})
