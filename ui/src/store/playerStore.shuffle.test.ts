import { beforeEach, describe, expect, it, vi } from "vitest"
import { patchSession } from "@/api/playback"
import { usePlayerStore } from "./playerStore"
import type { PlaybackEnvelope, PlaybackSession } from "@/api/types"

vi.mock("@/engine/playback", () => ({
  playerPlayback: {
    init: vi.fn(),
    load: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    setMediaSession: vi.fn(),
    registerMediaSessionHandlers: vi.fn(),
    consumePrefetched: vi.fn().mockReturnValue(null),
    prefetch: vi.fn().mockResolvedValue(undefined),
    cancelPrefetch: vi.fn(),
    clearPrefetched: vi.fn(),
    getEngineSnapshot: vi.fn().mockReturnValue({
      programDeck: "A",
      decks: { A: { id: "A", transport: "paused" }, B: { id: "B", transport: "paused" } },
    }),
  },
}))

vi.mock("@/api/playback", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/playback")>()
  return {
    ...actual,
    patchSession: vi.fn(),
    patchQueue: vi.fn(),
    postEvent: vi.fn().mockResolvedValue({}),
    refillAutoplay: vi.fn().mockResolvedValue({}),
    trackAudioUrl: (id: number) => `/audio/${id}`,
  }
})

function session(shuffleEnabled: boolean): PlaybackSession {
  return {
    id: "session-1",
    source_type: "playlist",
    autoplay_enabled: false,
    shuffle_enabled: shuffleEnabled,
    repeat_mode: "off",
  } as unknown as PlaybackSession
}

function envelope(shuffleEnabled: boolean): PlaybackEnvelope {
  return {
    session: session(shuffleEnabled),
    queue: {
      items: [],
      current_index: -1,
      current_item: null,
      upcoming: [],
      played: [],
      source_items: [],
      generated_items: [],
      autoplay_pool: [],
    },
  } as unknown as PlaybackEnvelope
}

describe("player shuffle", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    usePlayerStore.setState({ session: null, queue: null, error: null })
  })

  it("turns shuffle on for a linear session", async () => {
    usePlayerStore.setState({ session: session(false) })
    vi.mocked(patchSession).mockResolvedValue(envelope(true))

    await usePlayerStore.getState().setShuffle(true)

    expect(patchSession).toHaveBeenCalledWith("session-1", { shuffle_enabled: true })
    expect(usePlayerStore.getState().session?.shuffle_enabled).toBe(true)
  })

  it("leaves an already shuffled session alone instead of flipping it back", async () => {
    // A collection header's Shuffle button must shuffle, not toggle: playSource
    // carries shuffle_enabled over from the previous session, so a toggle here
    // would start the collection in linear order.
    usePlayerStore.setState({ session: session(true) })

    await usePlayerStore.getState().setShuffle(true)

    expect(patchSession).not.toHaveBeenCalled()
    expect(usePlayerStore.getState().session?.shuffle_enabled).toBe(true)
  })

  it("does nothing without a session to reorder", async () => {
    await usePlayerStore.getState().setShuffle(true)

    expect(patchSession).not.toHaveBeenCalled()
    expect(usePlayerStore.getState().error).toBeNull()
  })

  it("surfaces a failed patch instead of pretending the queue changed", async () => {
    usePlayerStore.setState({ session: session(false) })
    vi.mocked(patchSession).mockRejectedValue(new Error("nope"))

    await usePlayerStore.getState().setShuffle(true)

    expect(usePlayerStore.getState().error).toBe("nope")
    expect(usePlayerStore.getState().session?.shuffle_enabled).toBe(false)
  })

  it("still flips in both directions through toggleShuffle", async () => {
    usePlayerStore.setState({ session: session(true) })
    vi.mocked(patchSession).mockResolvedValue(envelope(false))

    await usePlayerStore.getState().toggleShuffle()

    expect(patchSession).toHaveBeenCalledWith("session-1", { shuffle_enabled: false })
    expect(usePlayerStore.getState().session?.shuffle_enabled).toBe(false)
  })
})
