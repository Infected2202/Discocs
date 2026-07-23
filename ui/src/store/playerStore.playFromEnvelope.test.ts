import { describe, it, expect, beforeEach, vi } from "vitest"
import { usePlayerStore } from "./playerStore"
import { playerPlayback } from "@/engine/playback"
import type { PlaybackEnvelope, QueueItem, TrackSummary } from "@/api/types"

// The playback facade is a browser/audio singleton — stub every method the store calls
// during playFromEnvelope → playTrack so nothing touches real audio.
vi.mock("@/engine/playback", () => ({
  playerPlayback: {
    init: vi.fn(),
    load: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    setMediaSession: vi.fn(),
    registerMediaSessionHandlers: vi.fn(),
    resumeAtSeconds: vi.fn(),
    consumePrefetched: vi.fn().mockReturnValue(null),
    prefetch: vi.fn().mockResolvedValue(undefined),
    cancelPrefetch: vi.fn(),
    clearPrefetched: vi.fn(),
    hasPrepared: vi.fn().mockReturnValue(false),
    handoverPrepared: vi.fn(),
    confirmHandover: vi.fn().mockResolvedValue(undefined),
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

describe("applyMediaSession — дедуп повторного вызова (A.4)", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(playerPlayback.setMediaSession).mockClear()
  })

  it("не переустанавливает MediaSession, если трек и артворк не изменились", async () => {
    await usePlayerStore.getState().playFromEnvelope(makeEnvelope())
    expect(playerPlayback.setMediaSession).toHaveBeenCalledTimes(1)

    // Имитируем повторный независимый триггер playTrack для того же трека —
    // например, дублирующийся handleTrackEnded или reconcileOnForeground.
    await usePlayerStore.getState().playTrack(10, { queueItemId: "a", recordStarted: false })

    expect(playerPlayback.setMediaSession).toHaveBeenCalledTimes(1)
  })

  it("переустанавливает MediaSession, когда трек реально сменился", async () => {
    await usePlayerStore.getState().playFromEnvelope(makeEnvelope())
    expect(playerPlayback.setMediaSession).toHaveBeenCalledTimes(1)

    await usePlayerStore.getState().playTrack(20, { queueItemId: "b", recordStarted: false })

    expect(playerPlayback.setMediaSession).toHaveBeenCalledTimes(2)
  })
})
