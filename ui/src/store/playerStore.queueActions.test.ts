import { beforeEach, describe, expect, it, vi } from "vitest"
import { audioEngine } from "@/engine/AudioEngine"
import { patchQueue } from "@/api/playback"
import { usePlayerStore } from "./playerStore"
import type { PlaybackEnvelope, QueueItem, TrackSummary } from "@/api/types"

vi.mock("@/engine/AudioEngine", () => ({
  audioEngine: {
    init: vi.fn(),
    load: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    setMediaSession: vi.fn(),
    registerMediaSessionHandlers: vi.fn(),
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

describe("player queue actions", () => {
  beforeEach(() => {
    vi.clearAllMocks()
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
})
