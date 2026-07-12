import { describe, it, expect, beforeEach, vi } from "vitest"
import { usePlayerStore } from "./playerStore"
import type { PlaybackEnvelope, QueueItem, TrackSummary } from "@/api/types"

// AudioEngine is a browser/audio singleton — stub every method the store calls
// during playFromEnvelope → playTrack so nothing touches real audio.
vi.mock("@/engine/AudioEngine", () => ({
  audioEngine: {
    init: vi.fn(),
    load: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    setMediaSession: vi.fn(),
    registerMediaSessionHandlers: vi.fn(),
    resumeAtSeconds: vi.fn(),
  },
}))

vi.mock("@/api/playback", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/playback")>()
  return {
    ...actual,
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
    artists: [{ id, name: `Artist ${id}` }],
    release: null,
    artwork: { url: null, source: "placeholder", placeholder: true },
    explicit: false,
    liked: false,
    actions: [],
  }
}

function makeItem(itemId: string, trackId: number): QueueItem {
  return { id: itemId, track_id: trackId, track: makeTrack(trackId) } as QueueItem
}

// Three-track playlist envelope; autoplay off so scheduleAutoplayRefill is a no-op.
function makeEnvelope(): PlaybackEnvelope {
  const items = [makeItem("a", 10), makeItem("b", 20), makeItem("c", 30)]
  return {
    session: {
      id: "s1",
      source_type: "playlist",
      autoplay_enabled: false,
      current_track_id: 10,
      current_queue_item_id: "a",
    },
    queue: {
      items,
      current_index: 0,
      current_item: items[0],
      upcoming: items.slice(1),
      played: [],
      source_items: items,
      generated_items: [],
      autoplay_pool: [],
    },
  } as unknown as PlaybackEnvelope
}

describe("playFromEnvelope — старт с preferredTrackId", () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it("без preferredTrackId стартует с current_item очереди", async () => {
    await usePlayerStore.getState().playFromEnvelope(makeEnvelope())

    expect(usePlayerStore.getState().currentTrackId).toBe(10)
    expect(usePlayerStore.getState().currentQueueItemId).toBe("a")
  })

  it("с preferredTrackId стартует именно с этого трека (весь плейлист, позиция на клике)", async () => {
    await usePlayerStore.getState().playFromEnvelope(makeEnvelope(), 30)

    expect(usePlayerStore.getState().currentTrackId).toBe(30)
    expect(usePlayerStore.getState().currentQueueItemId).toBe("c")
  })

  it("с preferredTrackId синхронизирует currentTrack (мета/заголовок/MediaSession)", async () => {
    await usePlayerStore.getState().playFromEnvelope(makeEnvelope(), 30)

    // Не оставляем currentTrack на current_item (трек 10) — иначе заголовок и
    // player bar показывают не тот трек, что реально играет.
    expect(usePlayerStore.getState().currentTrack?.id).toBe(30)
  })

  it("неизвестный preferredTrackId падает обратно на current_item", async () => {
    await usePlayerStore.getState().playFromEnvelope(makeEnvelope(), 999)

    expect(usePlayerStore.getState().currentTrackId).toBe(10)
  })
})
