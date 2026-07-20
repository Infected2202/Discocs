import { beforeEach, describe, expect, it, vi } from "vitest"
import { playerPlayback as audioEngine } from "@/engine/playback"
import { patchQueue } from "@/api/playback"
import { usePlayerStore } from "./playerStore"
import type { PlaybackEnvelope, QueueItem, TrackSummary } from "@/api/types"

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
    hasPrepared: vi.fn().mockReturnValue(false),
    handoverPrepared: vi.fn().mockResolvedValue({
      trackId: 20, queueItemId: "next", profileKey: "raw", outgoingDeck: "A", programDeck: "B",
    }),
    confirmHandover: vi.fn().mockResolvedValue(undefined),
  },
}))

vi.mock("@/api/playback", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/playback")>()
  return {
    ...actual,
    patchQueue: vi.fn(),
    patchSession: vi.fn(),
    postEvent: vi.fn().mockResolvedValue({}),
    refillAutoplay: vi.fn().mockResolvedValue({}),
    trackAudioUrl: (id: number) => `/audio/${id}`,
  }
})

function makeTrack(id: number): TrackSummary {
  return {
    id,
    title: `Track ${id}`,
    duration: 100,
    artists: [],
    release: null,
    artwork: { url: null, source: "placeholder", placeholder: true },
    explicit: false,
    liked: false,
    actions: [],
  }
}

function makeItem(id: string, trackId: number): QueueItem {
  return { id, track_id: trackId, track: makeTrack(trackId) } as QueueItem
}

function makeEnvelope(
  sessionId: string,
  items: QueueItem[],
  currentItemId: string,
): PlaybackEnvelope {
  const currentIndex = items.findIndex((item) => item.id === currentItemId)
  const currentItem = items[currentIndex]
  return {
    session: {
      id: sessionId,
      source_type: "track",
      autoplay_enabled: false,
      current_track_id: currentItem.track_id,
      current_queue_item_id: currentItem.id,
    },
    queue: {
      items,
      current_index: currentIndex,
      current_item: currentItem,
      upcoming: items.slice(currentIndex + 1),
      played: items.slice(0, currentIndex),
      source_items: items,
      generated_items: [],
      autoplay_pool: [],
    },
  } as unknown as PlaybackEnvelope
}

const audioCallbacks = vi.mocked(audioEngine.init).mock.calls[0][0]

describe("player queue actions", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(audioEngine.hasPrepared).mockReturnValue(false)
    localStorage.clear()
    usePlayerStore.setState({
      session: null,
      queue: null,
      playedHistory: [],
      currentTrackId: null,
      currentQueueItemId: null,
      currentTrack: null,
      playbackState: "idle",
      currentTime: 0,
      duration: 0,
      error: null,
      playbackProfile: { transcodingEnabled: false, bitrateKbps: 192, key: "raw" },
    })
  })

  it("adopts an instant mix around the playing track without restarting audio", async () => {
    const current = makeItem("old-current", 10)
    const initial = makeEnvelope("old-session", [current], current.id)
    usePlayerStore.setState({
      session: initial.session,
      queue: initial.queue,
      currentTrackId: 10,
      currentQueueItemId: current.id,
      currentTrack: current.track,
      playbackState: "playing",
      currentTime: 42,
    })
    const mixCurrent = makeItem("mix-current", 10)
    const mixNext = makeItem("mix-next", 30)

    await usePlayerStore.getState().adoptInstantMix(
      makeEnvelope("mix-session", [mixCurrent, mixNext], mixCurrent.id),
    )

    expect(audioEngine.load).not.toHaveBeenCalled()
    expect(audioEngine.play).not.toHaveBeenCalled()
    expect(usePlayerStore.getState()).toMatchObject({
      session: { id: "mix-session" },
      currentTrackId: 10,
      currentQueueItemId: "mix-current",
      playbackState: "playing",
      currentTime: 42,
    })
    expect(usePlayerStore.getState().queue?.items.map((item) => item.track_id)).toEqual([10, 30])
  })

  it("moves Play next immediately behind the current queue item", async () => {
    const current = makeItem("current", 10)
    const oldNext = makeItem("old-next", 20)
    const inserted = makeItem("inserted", 30)
    const initial = makeEnvelope("session", [current, oldNext], current.id)
    const appended = makeEnvelope("session", [current, oldNext, inserted], current.id)
    const moved = makeEnvelope("session", [current, inserted, oldNext], current.id)
    usePlayerStore.setState({
      session: initial.session,
      queue: initial.queue,
      currentTrackId: 10,
      currentQueueItemId: current.id,
      currentTrack: current.track,
    })
    vi.mocked(patchQueue)
      .mockResolvedValueOnce(appended)
      .mockResolvedValueOnce(moved)

    await usePlayerStore.getState().playNext(30, "Track 30")

    expect(patchQueue).toHaveBeenNthCalledWith(1, "session", {
      operation: "add",
      track_id: 30,
    })
    expect(patchQueue).toHaveBeenNthCalledWith(2, "session", {
      operation: "move",
      queue_item_id: "inserted",
      position: 1,
    })
    expect(usePlayerStore.getState().queue?.items.map((item) => item.track_id)).toEqual([10, 30, 20])
  })

  it("prefetches the next queue item only after the current track is fully buffered", () => {
    const current = makeItem("current", 10)
    const next = makeItem("next", 20)
    const envelope = makeEnvelope("session", [current, next], current.id)
    usePlayerStore.setState({
      session: envelope.session,
      queue: envelope.queue,
      currentTrackId: 10,
      currentQueueItemId: current.id,
      playbackProfile: { transcodingEnabled: false, bitrateKbps: 192, key: "raw" },
    })
    audioCallbacks.onFullyBuffered?.(10, "raw")

    expect(audioEngine.prefetch).toHaveBeenCalledWith(20, "/audio/20", "raw", "next")
  })

  it("plays a matching prefetched Blob without requesting a new network source", async () => {
    vi.mocked(audioEngine.consumePrefetched).mockReturnValueOnce("blob:track-20")
    usePlayerStore.setState({
      currentTrack: makeTrack(20),
      playbackProfile: { transcodingEnabled: true, bitrateKbps: 128, key: "mp3-128" },
    })

    await usePlayerStore.getState().playTrack(20, { queueItemId: "next", recordStarted: false })

    expect(audioEngine.load).toHaveBeenCalledWith("blob:track-20", 20, "mp3-128", true, "next")
  })

  it("hands over to a prepared deck exactly once without reloading incoming audio", async () => {
    const current = makeItem("current", 10)
    const next = makeItem("next", 20)
    const before = makeEnvelope("session", [current, next], current.id)
    const after = makeEnvelope("session", [current, next], next.id)
    usePlayerStore.setState({
      session: before.session,
      queue: before.queue,
      currentTrackId: 10,
      currentQueueItemId: current.id,
      currentTrack: current.track,
      playbackState: "playing",
    })
    vi.mocked(audioEngine.hasPrepared).mockReturnValue(true)
    vi.mocked(patchQueue).mockResolvedValueOnce(after)

    await usePlayerStore.getState().skipNext()

    expect(audioEngine.handoverPrepared).toHaveBeenCalledTimes(1)
    expect(audioEngine.load).not.toHaveBeenCalled()
    expect(patchQueue).toHaveBeenCalledWith("session", expect.objectContaining({
      operation: "handover",
      queue_item_id: "next",
      client_handover_id: expect.any(String),
    }))
    expect(audioEngine.confirmHandover).toHaveBeenCalledTimes(1)
    expect(usePlayerStore.getState().currentQueueItemId).toBe("next")
  })

  it("falls back to canonical jump when the incoming deck is unavailable", async () => {
    const current = makeItem("current", 10)
    const next = makeItem("next", 20)
    const before = makeEnvelope("session", [current, next], current.id)
    const after = makeEnvelope("session", [current, next], next.id)
    usePlayerStore.setState({
      session: before.session,
      queue: before.queue,
      currentTrackId: 10,
      currentQueueItemId: current.id,
      currentTrack: current.track,
    })
    vi.mocked(patchQueue).mockResolvedValueOnce(after)

    await usePlayerStore.getState().skipNext()

    expect(audioEngine.handoverPrepared).not.toHaveBeenCalled()
    expect(patchQueue).toHaveBeenCalledWith("session", {
      operation: "jump",
      queue_item_id: "next",
    })
    expect(audioEngine.load).toHaveBeenCalled()
  })

  it("retries canonical reconciliation without replaying an audible handover", async () => {
    const current = makeItem("current", 10)
    const next = makeItem("next", 20)
    const before = makeEnvelope("session", [current, next], current.id)
    const after = makeEnvelope("session", [current, next], next.id)
    usePlayerStore.setState({
      session: before.session,
      queue: before.queue,
      currentTrackId: 10,
      currentQueueItemId: current.id,
      currentTrack: current.track,
    })
    vi.mocked(audioEngine.hasPrepared).mockReturnValue(true)
    vi.mocked(patchQueue)
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(after)

    await usePlayerStore.getState().skipNext()
    await usePlayerStore.getState().skipNext()

    expect(audioEngine.handoverPrepared).toHaveBeenCalledTimes(1)
    expect(patchQueue).toHaveBeenCalledTimes(2)
    expect(vi.mocked(patchQueue).mock.calls[1]?.[1]).toEqual(
      vi.mocked(patchQueue).mock.calls[0]?.[1],
    )
    expect(audioEngine.confirmHandover).toHaveBeenCalledTimes(1)
  })

  it("keeps an already prepared source when the profile changes", () => {
    usePlayerStore.getState().setPlaybackProfile({
      transcodingEnabled: true,
      bitrateKbps: 128,
      key: "mp3-128",
    })

    expect(audioEngine.cancelPrefetch).toHaveBeenCalled()
    expect(audioEngine.clearPrefetched).not.toHaveBeenCalled()
  })
})
