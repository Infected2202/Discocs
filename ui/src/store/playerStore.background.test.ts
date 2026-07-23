import { beforeEach, describe, expect, it, vi } from "vitest"
import { playerPlayback as audioEngine } from "@/engine/playback"
import { fetchQueue, patchQueue, postEvent, refillAutoplay } from "@/api/playback"
import { usePlayerStore } from "./playerStore"
import type { PlaybackEnvelope, PlaybackSession, PlaybackQueue, QueueItem, TrackSummary } from "@/api/types"

vi.mock("@/engine/playback", () => ({
  playerPlayback: {
    init: vi.fn(),
    load: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    setMediaSession: vi.fn(),
    registerMediaSessionHandlers: vi.fn(),
    consumePrefetched: vi.fn().mockReturnValue(null),
    prefetch: vi.fn().mockResolvedValue(undefined),
    cancelPrefetch: vi.fn(),
    clearPrefetched: vi.fn(),
    seekToSeconds: vi.fn(),
    activateDjMode: vi.fn().mockResolvedValue(undefined),
    deactivateDjMode: vi.fn().mockResolvedValue(undefined),
    duration: 180,
  },
}))

vi.mock("@/api/playback", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/playback")>()
  return {
    ...actual,
    patchQueue: vi.fn(),
    postEvent: vi.fn().mockResolvedValue({}),
    refillAutoplay: vi.fn().mockResolvedValue({}),
    fetchQueue: vi.fn(),
    trackAudioUrl: (id: number) => `/audio/${id}`,
  }
})

function makeTrack(id: number): TrackSummary {
  return { id, title: `Track ${id}`, duration: 180, artists: [], release: null } as unknown as TrackSummary
}

function makeItem(id: string, trackId: number): QueueItem {
  return { id, track_id: trackId, track: makeTrack(trackId) } as QueueItem
}

function stubSession(currentTrackId = 10): PlaybackSession {
  return {
    id: "s1", source_type: "track", autoplay_enabled: false, current_track_id: currentTrackId,
  } as unknown as PlaybackSession
}

function stubQueue(items: QueueItem[], currentId: string): PlaybackQueue {
  const currentIndex = items.findIndex((i) => i.id === currentId)
  return {
    items,
    current_index: currentIndex,
    current_item: items[currentIndex],
    upcoming: items.slice(currentIndex + 1),
    played: items.slice(0, currentIndex),
    source_items: items,
    generated_items: [],
    autoplay_pool: [],
  } as unknown as PlaybackQueue
}

describe("player background / DJ engine behaviour", () => {
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
      playbackState: "playing",
      currentTime: 0,
      duration: 0,
      error: null,
      djEngineActive: false,
      playbackProfile: { transcodingEnabled: false, bitrateKbps: 192, key: "raw" },
    })
  })

  it("advances to the next track even when the completed telemetry never resolves", async () => {
    // Фоновый fetch может зависнуть — awaited postEvent раньше блокировал переход.
    vi.mocked(postEvent).mockReturnValueOnce(new Promise(() => undefined) as Promise<never>)
    const items = [makeItem("current", 10), makeItem("next", 20)]
    const nextEnvelope: PlaybackEnvelope = { session: stubSession(20), queue: stubQueue(items, "next") }
    vi.mocked(patchQueue).mockResolvedValue(nextEnvelope)
    usePlayerStore.setState({
      session: stubSession(),
      queue: stubQueue(items, "current"),
      currentTrackId: 10,
      currentQueueItemId: "current",
    })

    await usePlayerStore.getState().handleTrackEnded()

    // Переход выполнен, несмотря на «висящий» postEvent.
    expect(patchQueue).toHaveBeenCalledWith("s1", { operation: "jump", queue_item_id: "next" })
    expect(audioEngine.load).toHaveBeenCalled()
    expect(postEvent).toHaveBeenCalledWith(expect.objectContaining({ event_type: "completed" }))
  })

  it("stops at the deck end without auto-advancing while the DJ engine is active", async () => {
    const items = [makeItem("current", 10), makeItem("next", 20)]
    usePlayerStore.setState({
      session: stubSession(),
      queue: stubQueue(items, "current"),
      currentTrackId: 10,
      currentQueueItemId: "current",
      djEngineActive: true,
    })

    await usePlayerStore.getState().handleTrackEnded()

    expect(usePlayerStore.getState().playbackState).toBe("paused")
    expect(patchQueue).not.toHaveBeenCalled()
    expect(audioEngine.load).not.toHaveBeenCalled()
    expect(postEvent).toHaveBeenCalledWith(expect.objectContaining({ event_type: "completed" }))
  })

  it("activates and deactivates the DJ engine through the mixer facade", async () => {
    await usePlayerStore.getState().activateDj()
    expect(audioEngine.activateDjMode).toHaveBeenCalledOnce()
    expect(usePlayerStore.getState().djEngineActive).toBe(true)

    await usePlayerStore.getState().deactivateDj()
    expect(audioEngine.deactivateDjMode).toHaveBeenCalledOnce()
    expect(usePlayerStore.getState().djEngineActive).toBe(false)
  })

  it("mirrors the transport state onto the system media session", () => {
    const mediaSession = { playbackState: "none" as MediaSessionPlaybackState }
    Object.defineProperty(navigator, "mediaSession", { value: mediaSession, configurable: true })

    usePlayerStore.getState()._setPlaybackState("playing")
    expect(mediaSession.playbackState).toBe("playing")
    usePlayerStore.getState()._setPlaybackState("paused")
    expect(mediaSession.playbackState).toBe("paused")
    usePlayerStore.getState()._setPlaybackState("idle")
    expect(mediaSession.playbackState).toBe("none")
    // "loading" держим как "playing", чтобы статус не мигал в переходе.
    usePlayerStore.getState()._setPlaybackState("loading")
    expect(mediaSession.playbackState).toBe("playing")
  })

  it("resumes a stalled transition when returning to the foreground", () => {
    // Триггерим reconcileOnForeground через видимость: играем по стору, но
    // элемент на паузе и НЕ в конце — просто дозапускаем.
    Object.defineProperty(audioEngine, "paused", { value: true, configurable: true })
    Object.defineProperty(audioEngine, "duration", { value: 180, configurable: true })
    Object.defineProperty(audioEngine, "currentTime", { value: 42, configurable: true })
    usePlayerStore.setState({ playbackState: "playing", djEngineActive: false })

    Object.defineProperty(document, "hidden", { value: false, configurable: true })
    document.dispatchEvent(new Event("visibilitychange"))

    expect(audioEngine.play).toHaveBeenCalled()
  })
})

function createDeferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

// A.2: nothing in applyEnvelope's chain ever calls playTrack, so an autoplay
// refill that fills a previously exhausted queue used to leave playback
// stuck on "idle" forever. resumeIfIdleAfterRefill() (wired into
// scheduleAutoplayRefill, driven here via handleTrackEnded's exhausted-queue
// branch) must resume it — but only while still idle, still the same
// session, and not mid-DJ-mixing.
describe("auto-resume after autoplay refill (A.2)", () => {
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
      playbackState: "playing",
      currentTime: 0,
      duration: 0,
      error: null,
      djEngineActive: false,
      playbackProfile: { transcodingEnabled: false, bitrateKbps: 192, key: "raw" },
    })
  })

  function stubAutoplaySession(currentTrackId = 10): PlaybackSession {
    return {
      id: "s1", source_type: "track", autoplay_enabled: true, current_track_id: currentTrackId,
    } as unknown as PlaybackSession
  }

  it("resumes playback once an autoplay refill fills a previously exhausted queue", async () => {
    const exhausted = [makeItem("current", 10)]
    usePlayerStore.setState({
      session: stubAutoplaySession(),
      queue: stubQueue(exhausted, "current"),
      currentTrackId: 10,
      currentQueueItemId: "current",
      playbackState: "idle",
    })
    const refilled = [makeItem("current", 10), makeItem("gen", 30)]
    vi.mocked(fetchQueue).mockResolvedValue({
      session: stubAutoplaySession(),
      queue: stubQueue(refilled, "current"),
    })
    vi.mocked(patchQueue).mockResolvedValue({
      session: stubAutoplaySession(30),
      queue: stubQueue(refilled, "gen"),
    })

    await usePlayerStore.getState().handleTrackEnded()

    await vi.waitFor(() => expect(audioEngine.load).toHaveBeenCalled())
    expect(refillAutoplay).toHaveBeenCalledTimes(1)
    expect(usePlayerStore.getState().playbackState).not.toBe("idle")
    expect(usePlayerStore.getState().currentTrackId).toBe(30)
  })

  it("does not resume if the user paused before the refill's fetchQueue resolves", async () => {
    const exhausted = [makeItem("current", 10)]
    usePlayerStore.setState({
      session: stubAutoplaySession(),
      queue: stubQueue(exhausted, "current"),
      currentTrackId: 10,
      currentQueueItemId: "current",
      playbackState: "idle",
    })
    const deferredFetch = createDeferred<PlaybackEnvelope>()
    vi.mocked(fetchQueue).mockReturnValueOnce(deferredFetch.promise)

    await usePlayerStore.getState().handleTrackEnded()
    await vi.waitFor(() => expect(fetchQueue).toHaveBeenCalled())

    // User pauses while the refill's queue fetch is still in flight.
    usePlayerStore.setState({ playbackState: "paused" })

    const refilled = [makeItem("current", 10), makeItem("gen", 30)]
    deferredFetch.resolve({
      session: stubAutoplaySession(),
      queue: stubQueue(refilled, "current"),
    })
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(audioEngine.load).not.toHaveBeenCalled()
    expect(usePlayerStore.getState().playbackState).toBe("paused")
  })

  it("does not double-resume when two refill triggers fire in quick succession", async () => {
    const exhausted = [makeItem("current", 10)]
    usePlayerStore.setState({
      session: stubAutoplaySession(),
      queue: stubQueue(exhausted, "current"),
      currentTrackId: 10,
      currentQueueItemId: "current",
      playbackState: "idle",
    })
    const refilled = [makeItem("current", 10), makeItem("gen", 30)]
    vi.mocked(fetchQueue).mockResolvedValue({
      session: stubAutoplaySession(),
      queue: stubQueue(refilled, "current"),
    })
    vi.mocked(patchQueue).mockResolvedValue({
      session: stubAutoplaySession(30),
      queue: stubQueue(refilled, "gen"),
    })

    // Two overlapping triggers (e.g. a stray second "completed" firing) race
    // scheduleAutoplayRefill's existing refillInFlight guard.
    await Promise.all([
      usePlayerStore.getState().handleTrackEnded(),
      usePlayerStore.getState().handleTrackEnded(),
    ])
    await vi.waitFor(() => expect(audioEngine.load).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(refillAutoplay).toHaveBeenCalledTimes(1)
    expect(audioEngine.load).toHaveBeenCalledTimes(1)
  })
})
