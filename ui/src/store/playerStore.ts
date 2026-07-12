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
import { flowRefill, flowEvent } from "@/api/flow"
import { planRefill } from "./flowRefillRouting"
import {
  persistSessionId,
  loadPersistedSessionId,
  clearPersistedSessionId,
  persistPlaybackPosition,
  loadPersistedPlaybackPosition,
  clearPersistedPlaybackPosition,
  playbackPositionMatches,
} from "./sessionPersistence"
import { ApiError } from "@/api/client"
import { playerLog } from "@/lib/playerLogger"
import { hiresArtworkUrl } from "@/lib/artworkUrl"
import { throttle } from "@/lib/throttle"
import type { PlaybackEnvelope, PlaybackSession, PlaybackQueue, QueueItem, TrackSummary } from "@/api/types"

const STORAGE_KEY = "discocs.playerState.v1"
const REFILL_TRIGGER_EVENTS = new Set(["completed", "skipped", "liked", "disliked"])

function loadPersistedVolume(): { volume: number; muted: boolean } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      playerLog("volume", "no persisted state — defaults volume=1 muted=false")
      return { volume: 1, muted: false }
    }
    const parsed = JSON.parse(raw) as { volume?: number; muted?: boolean }
    const result = {
      volume: typeof parsed.volume === "number" ? parsed.volume : 1,
      muted: typeof parsed.muted === "boolean" ? parsed.muted : false,
    }
    playerLog("volume", "loaded from storage", result)
    return result
  } catch {
    playerLog("volume", "failed to load from storage — defaults volume=1 muted=false")
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
  playedHistory: QueueItem[]   // client-side accumulation — never cleared by server responses
  currentTrackId: number | null
  currentQueueItemId: string | null
  currentTrack: TrackSummary | null

  // Audio element state (written by AudioEngine)
  playbackState: PlaybackState
  currentTime: number
  duration: number
  /** Furthest downloaded position ahead of currentTime, as a 0-1 fraction of duration. */
  buffered: number
  volume: number
  muted: boolean
  error: string | null

  // UI state
  expanded: boolean

  // Actions
  playSource(type: string, id: number, label: string, preferredTrackId?: number): Promise<void>
  playTrack(trackId: number, opts?: { queueItemId?: string; recordStarted?: boolean }): Promise<void>
  jumpToQueueItem(queueItemId: string): Promise<void>
  jumpToAutoplayItem(poolItemId: string): Promise<void>
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
  playFromEnvelope(envelope: PlaybackEnvelope, preferredTrackId?: number): Promise<void>
  toggleExpanded(): void

  restoreSession(): Promise<void>

  // Internal — called by AudioEngine callbacks
  _setTime(currentTime: number, duration: number): void
  _setBuffered(fraction: number): void
  _setPlaybackState(state: PlaybackState): void
  _setError(message: string): void
}

const { volume: initVolume, muted: initMuted } = loadPersistedVolume()

export const usePlayerStore = create<PlayerState>((set, get) => {
  // Single-flight guard for scheduleAutoplayRefill — not store state on
  // purpose, it's a transient in-flight flag, not something a subscriber
  // should ever render off of.
  let refillInFlight = false

  // Кап записи времени в стор до ~4/сек. Chrome и так шлёт timeupdate ~4/сек,
  // но Firefox/Safari — заметно чаще; троттл делает частоту записи одинаковой
  // и не даёт вернуться шторму ре-рендеров, если наверху появится подписчик.
  // Trailing гарантирует, что последняя позиция перед паузой не потеряется.
  const throttledSetTime = throttle(
    (currentTime: number, duration: number) => get()._setTime(currentTime, duration),
    250
  )

  // Позиция пишется в localStorage раз в ~5с (trailing не теряет последнюю):
  // мобильный браузер может молча выгрузить вкладку в фоне, после перезагрузки
  // restoreSession вернёт и очередь, и место в треке.
  const throttledPersistPosition = throttle(
    (sessionId: string, queueItemId: string, trackId: number, seconds: number) => {
      persistPlaybackPosition({ sessionId, queueItemId, trackId, seconds })
    },
    5000
  )

  const persistCurrentPosition = (seconds: number) => {
    const { session, currentQueueItemId, currentTrackId } = get()
    if (!session?.id || !currentQueueItemId || currentTrackId == null) return
    persistPlaybackPosition({
      sessionId: session.id,
      queueItemId: currentQueueItemId,
      trackId: currentTrackId,
      seconds,
    })
  }

  const resetCurrentPosition = () => {
    // A trailing timeupdate from the previous playback window must not
    // overwrite this explicit start-over value a few seconds later.
    throttledPersistPosition.cancel()
    persistCurrentPosition(0)
  }

  // Wire AudioEngine callbacks once at store creation
  audioEngine.init({
    onTimeUpdate: (currentTime, duration) => {
      throttledSetTime(currentTime, duration)
      const { session, currentQueueItemId, currentTrackId } = get()
      if (session?.id && currentQueueItemId && currentTrackId != null && currentTime > 0) {
        throttledPersistPosition(session.id, currentQueueItemId, currentTrackId, currentTime)
      }
    },
    onBufferUpdate: (fraction) => get()._setBuffered(fraction),
    onPlaybackStateChange: (state) => get()._setPlaybackState(state),
    onEnded: () => get().handleTrackEnded(),
    onError: (message) => get()._setError(message),
  })

  audioEngine.setVolume(initVolume)
  audioEngine.setMuted(initMuted)

  // Точка невозврата для timeupdate — сохранить позицию сразу при уходе в фон
  // (следующий тик может уже не случиться, если вкладку выгрузят).
  if (typeof document !== "undefined") {
    const flushPosition = () => {
      if (audioEngine.currentTime > 0) persistCurrentPosition(audioEngine.currentTime)
    }
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) flushPosition()
    })
    globalThis.addEventListener("pagehide", flushPosition)
  }

  function addCurrentToHistory() {
    const { queue, currentQueueItemId } = get()
    if (!queue || !currentQueueItemId) return
    const item = queue.items.find((i) => i.id === currentQueueItemId)
    if (!item) return
    const existing = get().playedHistory
    if (existing.some((i) => i.id === item.id)) return
    set({ playedHistory: [...existing, item] })
  }

  function applyEnvelope(envelope: PlaybackEnvelope, resetHistory = false) {
    const { session, queue } = envelope
    const currentItem = queue.current_item ?? queue.items[0] ?? null
    set({
      session,
      queue,
      ...(resetHistory ? { playedHistory: [] } : {}),
      currentTrackId: session.current_track_id,
      currentQueueItemId: currentItem?.id ?? null,
      currentTrack: currentItem?.track ?? null,
    })
    return { session, queue, currentItem }
  }

  // Shared start-playback step for both session-create (playSource) and
  // pre-built-envelope (playFromEnvelope) flows: begin at the preferred track
  // if it's in the queue, else the session's current item, else the first item.
  async function playFirstOrPreferred(
    applied: ReturnType<typeof applyEnvelope>,
    preferredTrackId?: number,
  ) {
    const { queue, currentItem } = applied
    const preferred = preferredTrackId
      ? queue.items.find((item) => item.track_id === preferredTrackId)
      : null
    const first = preferred ?? currentItem ?? queue.items[0] ?? null
    if (first) {
      // applyEnvelope set currentTrack from the session's current_item. When we
      // start at a different (preferred) item, sync currentTrack too, or the
      // title / player bar / MediaSession stay on the old track (playTrack reads
      // get().currentTrack for metadata but never updates it itself).
      set({ currentTrack: first.track ?? null })
      await get().playTrack(first.track_id, {
        queueItemId: first.id,
        recordStarted: true,
      })
    }
  }

  async function scheduleAutoplayRefill(eventType?: string) {
    if (eventType && !REFILL_TRIGGER_EVENTS.has(eventType)) return
    const { session, currentTrackId, currentTrack } = get()
    if (!session?.id || !session.autoplay_enabled) return

    // Multiple triggers (skip, track-ended, like/dislike) can fire in close
    // succession. Without this guard, two overlapping refill calls can both
    // read the same queue state and append the same candidate twice.
    if (refillInFlight) return
    refillInFlight = true

    const { engine, sendEvent } = planRefill(session.source_type, eventType)

    try {
      if (engine === "flow") {
        if (sendEvent && currentTrackId) {
          // Apply the event BEFORE refilling: apply_flow_event persists skip
          // penalties / region switches to session state, and /flow/refill reads
          // that same state. Awaiting avoids a race where refill sees stale state.
          // A failed event must not block the refill, so it has its own catch.
          try {
            await flowEvent({
              session_id: session.id,
              event_type: eventType!,
              track_id: currentTrackId,
              artist_id: currentTrack?.artists?.[0]?.id ?? null,
              release_id: currentTrack?.release?.id ?? null,
            })
          } catch {
            // best-effort feedback — continue to refill regardless
          }
        }
        await flowRefill({ session_id: session.id, visible_buffer: 5 })
        await get().refreshQueue()
      } else {
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
      }
    } catch {
      // silently ignore refill errors — not user-facing
    } finally {
      refillInFlight = false
    }
  }

  return {
    session: null,
    queue: null,
    playedHistory: [],
    currentTrackId: null,
    currentQueueItemId: null,
    currentTrack: null,
    playbackState: "idle",
    currentTime: 0,
    duration: 0,
    buffered: 0,
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
        const repeatMode = session?.repeat_mode ?? "off"

        const envelope = await createSession({
          source_type: type,
          source_id: id,
          source_label: label,
          mode: shuffle ? "shuffle" : "linear",
          shuffle_enabled: shuffle,
          repeat_mode: repeatMode,
          autoplay_enabled: true,
        } satisfies CreateSessionParams)

        const applied = applyEnvelope(envelope, true)
        persistSessionId(envelope.session.id)
        await playFirstOrPreferred(applied, preferredTrackId)

        scheduleAutoplayRefill()
      } catch (err) {
        set({ error: (err as Error).message })
      }
    },

    async playTrack(trackId, { queueItemId, recordStarted = true } = {}) {
      set({ error: null, playbackState: "loading" })
      if (queueItemId) set({ currentQueueItemId: queueItemId })
      set({ currentTrackId: trackId })
      // A deliberate start is a new playback occurrence. Persist zero now so
      // an older position for the same track can never leak into this queue item.
      resetCurrentPosition()

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
        const artwork = hiresArtworkUrl(track.artwork?.url, 512)
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
      addCurrentToHistory()
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

    async jumpToAutoplayItem(poolItemId) {
      const { session, queue } = get()
      if (!session?.id || !queue) return
      const idx = queue.autoplay_pool.findIndex((i) => i.id === poolItemId)
      if (idx === -1) return
      const trackIds = queue.autoplay_pool.slice(0, idx + 1).map((i) => i.track_id)
      try {
        // Add all pool items up to (and including) the clicked one into the queue
        const envelope = await patchQueue(session.id, { operation: "add", track_ids: trackIds })
        applyEnvelope(envelope)
        // Find the added item for the target track (search upcoming from the end)
        const targetTrackId = queue.autoplay_pool[idx].track_id
        const target = [...envelope.queue.upcoming].reverse().find((i) => i.track_id === targetTrackId)
        if (target) await get().jumpToQueueItem(target.id)
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
      // Optimistic update — avoids a visible jump back to the stale currentTime
      // before the next native `timeupdate` tick (~250ms) catches up.
      const { duration } = get()
      if (Number.isFinite(duration) && duration > 0) {
        set({ currentTime: fraction * duration })
      }
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
        resetCurrentPosition()
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
      const newMode = session.repeat_mode === "one" ? "off" : "one"
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
      playerLog("volume", "setVolume", { raw: v, clamped })
      audioEngine.setVolume(clamped)
      set({ volume: clamped })
      persistVolume(clamped, get().muted)
    },

    toggleMute() {
      const muted = !get().muted
      playerLog("volume", "toggleMute", { muted, volume: get().volume })
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

      addCurrentToHistory()
      // The backend intentionally keeps the completed item as current. Do not
      // resurrect its near-duration position if the tab is discarded now.
      throttledPersistPosition.cancel()
      clearPersistedPlaybackPosition()

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

    async playFromEnvelope(envelope, preferredTrackId) {
      set({ error: null })
      try {
        const applied = applyEnvelope(envelope, true)
        persistSessionId(envelope.session.id)
        await playFirstOrPreferred(applied, preferredTrackId)
        scheduleAutoplayRefill()
      } catch (err) {
        set({ error: (err as Error).message })
      }
    },

    async restoreSession() {
      const sessionId = loadPersistedSessionId()
      if (!sessionId) return
      try {
        const envelope = await fetchQueue(sessionId)
        applyEnvelope(envelope)
        const { currentTrackId, currentTrack } = get()
        if (currentTrackId) {
          audioEngine.load(trackAudioUrl(currentTrackId))
          audioEngine.setVolume(get().volume)
          audioEngine.setMuted(get().muted)
          // Вернуть сохранённую позицию — сработает, когда пользователь
          // нажмёт play (метаданные при preload="none" грузятся только тогда).
          const persisted = loadPersistedPlaybackPosition()
          const sessionId = get().session?.id
          const queueItemId = get().currentQueueItemId
          if (
            persisted
            && sessionId
            && queueItemId
            && playbackPositionMatches(persisted, { sessionId, queueItemId, trackId: currentTrackId })
            && persisted.seconds > 0
          ) {
            audioEngine.resumeAtSeconds(persisted.seconds)
            set({ currentTime: persisted.seconds })
          }
          if (currentTrack) {
            audioEngine.setMediaSession(currentTrack, hiresArtworkUrl(currentTrack.artwork?.url, 512))
          }
          audioEngine.registerMediaSessionHandlers({
            play: () => get().togglePlay(),
            pause: () => get().togglePlay(),
            nexttrack: () => get().skipNext(),
            previoustrack: () => get().skipPrevious(),
          })
        }
      } catch (err) {
        // Only clear on 404 — session is gone for good.
        // Network errors (server down, connection refused) leave the id intact.
        if (err instanceof ApiError && err.status === 404) {
          clearPersistedSessionId()
        }
      }
    },

    toggleExpanded() {
      set((s) => ({ expanded: !s.expanded }))
    },

    // --- Internal callbacks from AudioEngine ---
    _setTime(currentTime, duration) {
      set({ currentTime, duration })
    },
    _setBuffered(fraction) {
      set({ buffered: fraction })
    },
    _setPlaybackState(state) {
      set({ playbackState: state })
    },
    _setError(message) {
      set({ error: message, playbackState: "error" })
    },
  }
})
