import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { HtmlMediaDeckSource } from "./HtmlMediaDeckSource"

class MockAudio {
  src = ""
  preload = ""
  duration = Number.NaN
  currentTime = 0
  playbackRate = 1
  preservesPitch = false
  loop = false
  buffered = { length: 0, start: vi.fn(), end: vi.fn() }
  load = vi.fn()
  play = vi.fn().mockResolvedValue(undefined)
  pause = vi.fn()
}

describe("HtmlMediaDeckSource lifecycle", () => {
  const disconnect = vi.fn()
  const revokeObjectURL = vi.fn()
  const createObjectURL = vi.fn(() => "blob:owned-audio")
  let audio: MockAudio

  beforeEach(() => {
    disconnect.mockClear()
    revokeObjectURL.mockClear()
    createObjectURL.mockClear()
    vi.stubGlobal("Audio", function () {
      audio = new MockAudio()
      return audio
    })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      blob: vi.fn().mockResolvedValue(new Blob(["audio"])),
    }))
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL })
  })

  afterEach(() => vi.unstubAllGlobals())

  it("releases its media element, node and owned object URL exactly once", async () => {
    const context = {
      currentTime: 0,
      createMediaElementSource: vi.fn(() => ({ disconnect })),
    } as unknown as AudioContext
    const source = new HtmlMediaDeckSource(context)
    await source.load(
      { url: "/audio/1", trackId: 1, fetchAsBlob: true },
      new AbortController().signal,
    )

    await source.release()
    await source.release()

    expect(fetch).toHaveBeenCalledWith("/audio/1", expect.objectContaining({ credentials: "same-origin" }))
    expect(audio.pause).toHaveBeenCalledTimes(1)
    expect(audio.src).toBe("")
    expect(audio.load).toHaveBeenCalledTimes(2)
    expect(disconnect).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:owned-audio")
  })
})
