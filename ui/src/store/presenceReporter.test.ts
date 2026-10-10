import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { PresenceReport } from "@/api/playback"
import { playerPlayback as audioEngine } from "@/engine/playback"
import { getActivePresenceReporter, stopActivePresence } from "@/lib/presence"
import { usePlayerStore } from "./playerStore"
import { startPresenceReporting } from "./presenceReporter"

vi.mock("@/engine/playback", () => ({
  playerPlayback: {
    init: vi.fn(),
    load: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    seek: vi.fn(),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    setMediaSession: vi.fn(),
    registerMediaSessionHandlers: vi.fn(),
    consumePrefetched: vi.fn().mockReturnValue(null),
    prefetch: vi.fn().mockResolvedValue(undefined),
    cancelPrefetch: vi.fn(),
    clearPrefetched: vi.fn(),
    prefetchAhead: vi.fn().mockResolvedValue(undefined),
    currentTime: 0,
  },
}))

const engine = audioEngine as unknown as { currentTime: number }

function makeTransport() {
  return {
    send: vi.fn().mockResolvedValue({ status: "ok" }),
    beacon: vi.fn().mockReturnValue(true),
  }
}

function sentStates(send: ReturnType<typeof vi.fn>): Array<[string, number, number]> {
  return send.mock.calls.map(([report]) => {
    const r = report as PresenceReport
    return [r.state, r.track_id, r.position_ms]
  })
}

describe("startPresenceReporting — the logged-in player reports presence", () => {
  let stop: (() => void) | null = null

  beforeEach(() => {
    engine.currentTime = 0
    usePlayerStore.setState({
      currentTrackId: null,
      playbackState: "idle",
      seekBuffering: false,
      seekGeneration: 0,
      currentTime: 0,
      duration: 200,
    })
  })

  afterEach(() => {
    stop?.()
    stop = null
  })

  it("reports start, pause, resume, seek and stop from player state", () => {
    const transport = makeTransport()
    stop = startPresenceReporting(transport)

    usePlayerStore.setState({ currentTrackId: 5, playbackState: "loading" })
    usePlayerStore.setState({ playbackState: "playing" })
    engine.currentTime = 30
    usePlayerStore.setState({ currentTime: 30 })
    usePlayerStore.setState({ playbackState: "paused" })
    usePlayerStore.setState({ playbackState: "playing" })
    usePlayerStore.getState().seek(0.5)
    usePlayerStore.setState({ currentTime: 101 })
    engine.currentTime = 200
    usePlayerStore.setState({ playbackState: "idle" })

    expect(sentStates(transport.send)).toEqual([
      ["starting", 5, 0],
      ["paused", 5, 30_000],
      ["playing", 5, 30_000],
      ["playing", 5, 100_000],
      ["stopped", 5, 200_000],
    ])
  })

  it("reports the next queue track as a new start", () => {
    const transport = makeTransport()
    stop = startPresenceReporting(transport)

    usePlayerStore.setState({ currentTrackId: 5, playbackState: "playing" })
    engine.currentTime = 150
    usePlayerStore.setState({ currentTrackId: 6 })
    usePlayerStore.setState({ playbackState: "loading" })
    engine.currentTime = 0
    usePlayerStore.setState({ playbackState: "playing" })

    expect(sentStates(transport.send)).toEqual([
      ["starting", 5, 0],
      ["starting", 6, 0],
    ])
  })

  it("sends nothing before it is started or after it is stopped", () => {
    const transport = makeTransport()
    usePlayerStore.setState({ currentTrackId: 5, playbackState: "playing" })
    usePlayerStore.setState({ currentTrackId: null, playbackState: "idle" })

    stop = startPresenceReporting(transport)
    stop()
    stop = null
    usePlayerStore.setState({ currentTrackId: 7, playbackState: "playing" })
    globalThis.dispatchEvent(new Event("pagehide"))

    expect(transport.send).not.toHaveBeenCalled()
    expect(transport.beacon).not.toHaveBeenCalled()
    expect(getActivePresenceReporter()).toBeNull()
  })

  it("beacons stopped on pagehide", () => {
    const transport = makeTransport()
    stop = startPresenceReporting(transport)
    usePlayerStore.setState({ currentTrackId: 5, playbackState: "playing" })
    engine.currentTime = 42

    globalThis.dispatchEvent(new Event("pagehide"))

    expect(transport.beacon).toHaveBeenCalledWith({ track_id: 5, state: "stopped", position_ms: 42_000 })
  })

  it("never throws into the player when reporting fails", async () => {
    const transport = makeTransport()
    transport.send.mockRejectedValue(new TypeError("offline"))
    stop = startPresenceReporting(transport)

    expect(() => usePlayerStore.setState({ currentTrackId: 5, playbackState: "playing" })).not.toThrow()
    expect(() => usePlayerStore.setState({ playbackState: "paused" })).not.toThrow()
    await Promise.resolve()

    expect(transport.send).toHaveBeenCalledTimes(2)
  })

  it("lets logout report stopped through the active reporter", async () => {
    const transport = makeTransport()
    stop = startPresenceReporting(transport)
    usePlayerStore.setState({ currentTrackId: 5, playbackState: "playing" })

    await stopActivePresence()

    expect(sentStates(transport.send).at(-1)).toEqual(["stopped", 5, 0])
  })
})
