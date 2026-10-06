import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { playerPlayback as audioEngine } from "@/engine/playback"
import { postEvent } from "@/api/playback"
import { usePlayerStore } from "./playerStore"
import type { PlaybackSession } from "@/api/types"

vi.mock("@/engine/playback", () => ({
  playerPlayback: {
    init: vi.fn(),
    load: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    seek: vi.fn(),
    seekToSeconds: vi.fn(),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    setMediaSession: vi.fn(),
    registerMediaSessionHandlers: vi.fn(),
    consumePrefetched: vi.fn().mockReturnValue(null),
    prefetch: vi.fn().mockResolvedValue(undefined),
    cancelPrefetch: vi.fn(),
    clearPrefetched: vi.fn(),
  },
}))

vi.mock("@/api/playback", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/playback")>()),
  postEvent: vi.fn().mockResolvedValue({}),
}))

const callbacks = vi.mocked(audioEngine.init).mock.calls[0][0]
let playNumber = 0

function thresholdEvents() {
  return vi.mocked(postEvent).mock.calls
    .map(([event]) => event)
    .filter((event) => event.event_type === "play_threshold_reached")
}

/** Steady playback: one timeupdate per wall-clock second. */
function playSeconds(from: number, to: number) {
  for (let position = from; position <= to; position += 1) {
    callbacks.onTimeUpdate(position, 200)
    vi.advanceTimersByTime(1000)
  }
}

describe("listen threshold", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(postEvent).mockClear()
    playNumber += 1
    usePlayerStore.setState({
      session: { id: "s1" } as unknown as PlaybackSession,
      // Another queue item per test: a fresh play for the store's tracker.
      currentQueueItemId: `q${playNumber}`,
      currentTrackId: 7,
      playbackState: "playing",
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("reports play_threshold_reached once half the track has been played", () => {
    playSeconds(0, 150)

    const events = thresholdEvents()
    expect(events).toHaveLength(1)
    expect(events[0]).toMatchObject({ session_id: "s1", track_id: 7, position_seconds: 100, duration_seconds: 200 })
    expect(events[0].play_fraction).toBeCloseTo(0.5)
  })

  it("does not report a play that was sought through", () => {
    playSeconds(0, 20)
    callbacks.onTimeUpdate(180, 200)
    playSeconds(181, 200)

    expect(thresholdEvents()).toHaveLength(0)
  })

  it("does not count a seek made while paused", () => {
    playSeconds(0, 60)
    callbacks.onPlaybackStateChange("paused")
    vi.advanceTimersByTime(600_000)
    callbacks.onPlaybackStateChange("playing")
    playSeconds(170, 200)

    expect(thresholdEvents()).toHaveLength(0)
  })
})
