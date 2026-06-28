import { create } from "zustand"
import { audioEngine, type PlaybackState } from "@/engine/AudioEngine"
import {
  createSession,
  fetchQueue,
  patchQueue,
  patchSession,
  postEvent,
  refillAutoplay,
  trackAudioUrl,
  type CreateSessionParams,
} from "@/api/playback"
import type { PlaybackEnvelope, PlaybackSession, PlaybackQueue, QueueItem, TrackSummary } from "@/api/types"

const STORAGE_KEY = "discocs.playerState.v1"
const REFILL_TRIGGER_EVENTS = new Set(["completed", "skipped", "liked", "disliked"])

function loadPersistedVolume(): { volume: number; muted: boolean } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { volume: 1, muted: false }
    const parsed = JSON.parse(raw) as { volume?: number; muted?: boolean }
    return {
      volume: typeof parsed.volume === "number" ? parsed.volume : 1,
      muted: typeof parsed.muted === "boolean" ? parsed.muted : false,
    }
  } catch {
    return { volume: 1, muted: false }
  }
}

function persistVolume(volume: number, muted: boolean) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ volume, muted }))
  } catch {
    // ignore
  }
}

interface PlayerState {
  // Server state
  session: PlaybackSession | null
  queue: PlaybackQueue | null
  currentTrackId: number | null
  currentQueueItemId: string | null
  currentTrack: TrackSummary | null

  // Audio element state (written by AudioEngine)
  playbackState: PlaybackState
  currentTime: number
  duration: number
  volume: number
  muted: boolean
  error: string | null

  // UI state
  expanded: boolean

  // Actions
  playSource(type: string, id: number, label: string, preferredTrackId?: number): Promise<void>
  playTrack(trackId: number, opts?: { queueItemId?: string; recordStarted?: boolean }): Promise<void>
  jumpToQueueItem(queueItemId: string): Promise<void>
  togglePlay(): void
  seek(fraction: number): void
  skipNext(): Promise<void>
  skipPrevious(): Promise<void>
  toggleShuffle(): Promise<void>
  toggleRepeatOne(): Promise<void>
  toggleAutoplay(): Promise<void>
  setAutoplayChip(chip: string): Promise<void>
  setVolume(v: number): void
  toggleMute(): void
  refreshQueue(): Promise<void>
  recordEvent(eventType: string, extra?: Record<string, unknown>): Promise<void>
  handleTrackEnded(): Promise<void>
  toggleExpanded(): void

  // Internal — called by AudioEngine callbacks
  _setTime(currentTime: number, duration: number): void
  _setPlaybackState(state: PlaybackState): void
  _setError(message: string): void
}

const { volume: initVolume, muted: initMuted } = loadPersistedVolume()

export const usePlayerStore = create<PlayerState>((set, get) => {
  // Wire AudioEngine callbacks once at store creation
  audioEngine.init({
    onTimeUpdate: (currentTime, duration) => get()._setTime(currentTime, duration),
    onPlaybackStateChange: (state) => get()._setPlaybackState(state),
    onEnded: () => get().handleTrackEnded(),
    onError: (message) => get()._setError(message),
  })

  audioEngine.setVolume(initVolume)
  audioEngine.setMuted(initMuted)

  function applyEnvelope(envelope: PlaybackEnvelope) {
    const { session, queue } = envelope
    const currentItem = queue.current_item ?? queue.items[0] ?? null
    set({
      session,
      queue,
      currentTrackId: session.current_track_id,
      currentQueueItemId: currentItem?.id ?? null,
      currentTrack: (currentItem?.track as TrackSummary | null) ?? null,
    })
    return { session, queue, currentItem }
  }

  async function scheduleAutoplayRefill(eventType?: string) {
    if (eventType && !REFILL_TRIGGER_EVENTS.has(eventType)) return
    const { session } = get()
    if (!session?.id || !session.autoplay_enabled) return
    try {
      await refillAutoplay({
        session_id: session.id,
        visible_buffer: 5,
        candidate_count: 50,
        settings: {
          autoplay_visible_buffer: 5,
          autoplay_candidate_count: 50,
          autoplay_preference_chip: session.settings?.autoplay_preference_chip ?? "All",
        },
      })
      await get().refreshQueue()
    } catch {
      // silently ignore refill errors — not user-facing
    }
  }

  return {
    session: null,
    queue: null,
    currentTrackId: null,
    currentQueueItemId: null,
    currentTrack: null,
    playbackState: "idle",
    currentTime: 0,
    duration: 0,
    volume: initVolume,
    muted: initMuted,
    error: null,
    expanded: false,

    // --- Playback actions ---

    async playSource(type, id, label, preferredTrackId) {
      set({ error: null })
      try {
        const { session } = get()
        const shuffle = session?.shuffle_enabled ?? false
        const repeatMode = session?.repeat_mode ?? "none"

        const envelope = await createSession({
          source_type: type,
          source_id: id,
          source_label: label,
          mode: shuffle ? "shuffle" : "linear",
          shuffle_enabled: shuffle,
          repeat_mode: repeatMode,
          autoplay_enabled: true,
        } satisfies CreateSessionParams)

        const { queue, currentItem } = applyEnvelope(envelope)

        const preferred = preferredTrackId
          ? queue.items.find((item) => item.track_id === preferredTrackId)
          : null
        const first = preferred ?? currentItem ?? queue.items[0] ?? null

        if (first) {
          await get().playTrack(first.track_id, {
            queueItemId: first.id,
            recordStarted: true,
          })
        }

        scheduleAutoplayRefill()
      } catch (err) {
        set({ error: (err as Error).message })
      }
    },

    async playTrack(trackId, { queueItemId, recordStarted = true } = {}) {
      set({ error: null, playbackState: "loading" })
      if (queueItemId) set({ currentQueueItemId: queueItemId })
      set({ currentTrackId: trackId })

      const url = trackAudioUrl(trackId)
      audioEngine.load(url)
      audioEngine.setVolume(get().volume)
      audioEngine.setMuted(get().muted)

      try {
        await audioEngine.play()
        if (recordStarted) {
          await get().recordEvent("track_started")
        }
      } catch (err) {
        set({ error: (err as Error).message, playbackState: "error" })
      }

      // Update Media Session metadata if we have track info
      const track = get().currentTrack
      if (track) {
        const artwork = track.artwork?.url ?? undefined
        audioEngine.setMediaSession(track, artwork)
      }
      audioEngine.registerMediaSessionHandlers({
        play: () => get().togglePlay(),
        pause: () => get().togglePlay(),
        nexttrack: () => get().skipNext(),
        previoustrack: () => get().skipPrevious(),
      })
    },

    async jumpToQueueItem(queueItemId) {
      const { session } = get()
      if (!session?.id) return
      try {
        const envelope = await patchQueue(session.id, {
          operation: "jump",
          queue_item_id: queueItemId,
        })
        const { currentItem } = applyEnvelope(envelope)
        if (currentItem) {
          await get().playTrack(currentItem.track_id, {
            queueItemId: currentItem.id,
            recordStarted: false,
          })
        }
      } catch (err) {
        set({ error: (err as Error).message })
      }
    },

    togglePlay() {
      if (audioEngine.paused) {
        audioEngine.play().catch((err: Error) => set({ error: err.message, playbackState: "error" }))
      } else {
        audioEngine.pause()
      }
    },

    seek(fraction) {
      audioEngine.seek(fraction)
    },

    async skipNext() {
      const { queue, currentQueueItemId, session } = get()
      if (!queue || !session) return

      await get().recordEvent("skipped", {
        position_seconds: audioEngine.currentTime,
        duration_seconds: audioEngine.duration,
      })

      const items = queue.items
      const idx = items.findIndex((i) => i.id === currentQueueItemId)
      const next: QueueItem | undefined = items[idx + 1]
      if (next) {
        await get().jumpToQueueItem(next.id)
      }
      scheduleAutoplayRefill("skipped")
    },

    async skipPrevious() {
      const { queue, currentQueueItemId } = get()
      if (!queue) return

      // If more than 3s played, restart current track
      if (audioEngine.currentTime > 3) {
        audioEngine.seekToSeconds(0)
        return
      }

      const items = queue.items
      const idx = items.findIndex((i) => i.id === currentQueueItemId)
      const prev: QueueItem | undefined = items[idx - 1]
      if (prev) await get().jumpToQueueItem(prev.id)
    },

    async toggleShuffle() {
      const { session } = get()
      if (!session?.id) return
      const newShuffle = !session.shuffle_enabled
      try {
        const envelope = await patchSession(session.id, { shuffle_enabled: newShuffle })
        applyEnvelope(envelope)
      } catch (err) {
        set({ error: (err as Error).message })
      }
    },

    async toggleRepeatOne() {
      const { session } = get()
      if (!session?.id) return
      const newMode = session.repeat_mode === "one" ? "none" : "one"
      try {
        const envelope = await patchSession(session.id, { repeat_mode: newMode })
        applyEnvelope(envelope)
      } catch (err) {
        set({ error: (err as Error).message })
      }
    },

    async toggleAutoplay() {
      const { session } = get()
      if (!session?.id) return
      try {
        const envelope = await patchSession(session.id, {
          autoplay_enabled: !session.autoplay_enabled,
        })
        applyEnvelope(envelope)
        if (envelope.session.autoplay_enabled) scheduleAutoplayRefill()
      } catch (err) {
        set({ error: (err as Error).message })
      }
    },

    async setAutoplayChip(chip) {
      const { session } = get()
      if (!session?.id) return
      try {
        const envelope = await patchSession(session.id, {
          settings: { ...session.settings, autoplay_preference_chip: chip },
        })
        applyEnvelope(envelope)
        scheduleAutoplayRefill()
      } catch (err) {
        set({ error: (err as Error).message })
      }
    },

    setVolume(v) {
      const clamped = Math.max(0, Math.min(1, v))
      audioEngine.setVolume(clamped)
      set({ volume: clamped })
      persistVolume(clamped, get().muted)
    },

    toggleMute() {
      const muted = !get().muted
      audioEngine.setMuted(muted)
      set({ muted })
      persistVolume(get().volume, muted)
    },

    async refreshQueue() {
      const { session } = get()
      if (!session?.id) return
      try {
        const envelope = await fetchQueue(session.id)
        applyEnvelope(envelope)
      } catch {
        // ignore
      }
    },

    async recordEvent(eventType, extra = {}) {
      const { session, currentTrackId, currentQueueItemId } = get()
      if (!currentTrackId) return
      try {
        await postEvent({
          session_id: session?.id ?? "",
          queue_item_id: currentQueueItemId ?? undefined,
          track_id: currentTrackId,
          event_type: eventType,
          ...extra,
        })
        scheduleAutoplayRefill(eventType)
      } catch {
        // fire-and-forget — playback events are best-effort
      }
    },

    async handleTrackEnded() {
      const { session, queue, currentQueueItemId } = get()

      await get().recordEvent("completed", {
        position_seconds: audioEngine.duration,
        duration_seconds: audioEngine.duration,
        play_fraction: 1,
      })

      // Repeat one
      if (session?.repeat_mode === "one") {
        audioEngine.seekToSeconds(0)
        audioEngine.play().catch((err: Error) => set({ error: err.message }))
        return
      }

      // Play next in queue
      if (queue) {
        const items = queue.items
        const idx = items.findIndex((i) => i.id === currentQueueItemId)
        const next: QueueItem | undefined = items[idx + 1]
        if (next) {
          await get().jumpToQueueItem(next.id)
          return
        }
      }

      // Queue exhausted
      set({ playbackState: "idle" })
      scheduleAutoplayRefill("completed")
    },

    toggleExpanded() {
      set((s) => ({ expanded: !s.expanded }))
    },

    // --- Internal callbacks from AudioEngine ---
    _setTime(currentTime, duration) {
      set({ currentTime, duration })
    },
    _setPlaybackState(state) {
      set({ playbackState: state })
    },
    _setError(message) {
      set({ error: message, playbackState: "error" })
    },
  }
})
