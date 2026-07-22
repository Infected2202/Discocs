import { create } from "zustand"
import {
  playerPlayback as audioEngine,
  type PlayerBufferedRange as BufferedRange,
  type PlaybackState,
  type DeckId,
} from "@/engine/playback"
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
import type { PlaybackProfile } from "@/api/settings"
import { useUIStore } from "./uiStore"

const STORAGE_KEY = "discocs.playerState.v1"
const REFILL_TRIGGER_EVENTS = new Set(["completed", "skipped", "liked", "disliked"])
let handoverSequence = 0

function createClientHandoverId(sessionId: string, queueItemId: string): string {
  handoverSequence += 1
  return `handover-${sessionId}-${queueItemId}-${Date.now().toString(36)}-${handoverSequence}`
}

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
  /** Downloaded segments as 0-1 fractions of duration (gaps are preserved). */
  bufferedRanges: BufferedRange[]
  volume: number
  muted: boolean
  error: string | null
  playbackProfile: PlaybackProfile

  // UI state
  expanded: boolean
  /** DJ engine (Web Audio graph) is active. Panel visibility lives in uiStore. */
  djEngineActive: boolean

  // Actions
  playSource(type: string, id: number, label: string, preferredTrackId?: number): Promise<void>
  playTrack(trackId: number, opts?: { queueItemId?: string; recordStarted?: boolean }): Promise<void>
  jumpToQueueItem(queueItemId: string): Promise<void>
  jumpToAutoplayItem(poolItemId: string): Promise<void>
  togglePlay(): void
  activateDj(): Promise<void>
  deactivateDj(): Promise<void>
  toggleDjDeck(deck: DeckId): Promise<void>
  seek(fraction: number): void
  skipNext(): Promise<void>
  skipPrevious(): Promise<void>
  toggleShuffle(): Promise<void>
  toggleRepeatOne(): Promise<void>
  toggleAutoplay(): Promise<void>
  setAutoplayChip(chip: string): Promise<void>
  setVolume(v: number): void
  setPlaybackProfile(profile: PlaybackProfile): void
  toggleMute(): void
  refreshQueue(): Promise<void>
  recordEvent(eventType: string, extra?: Record<string, unknown>): Promise<void>
  handleTrackEnded(): Promise<void>
  playFromEnvelope(envelope: PlaybackEnvelope, preferredTrackId?: number): Promise<void>
  adoptInstantMix(envelope: PlaybackEnvelope): Promise<void>
  playNext(trackId: number, sourceLabel?: string): Promise<void>
  prepareDjDeck(trackId: number, deck: DeckId): Promise<void>
  toggleExpanded(): void

  restoreSession(): Promise<void>
  resetForLogout(): void

  // Internal — called by AudioEngine callbacks
  _setTime(currentTime: number, duration: number): void
  _setBuffered(ranges: BufferedRange[]): void
  _setPlaybackState(state: PlaybackState): void
  _setError(message: string): void
}

const { volume: initVolume, muted: initMuted } = loadPersistedVolume()

export const usePlayerStore = create<PlayerState>((set, get) => {
  // Single-flight guard for scheduleAutoplayRefill — not store state on
  // purpose, it's a transient in-flight flag, not something a subscriber
  // should ever render off of.
  let refillInFlight = false
  let fullyBufferedSource: { trackId: number; profileKey: string } | null = null
  let pendingHandover: {
    sessionId: string
    queueItemId: string
    clientHandoverId: string
  } | null = null
  const incomingStartedQueueItems = new Set<string>()

  function scheduleNextPrefetch() {
    const { queue, currentQueueItemId, currentTrackId, session, playbackProfile } = get()
    if (
      !queue || !currentQueueItemId || currentTrackId === null
      || session?.repeat_mode === "one"
      || fullyBufferedSource?.trackId !== currentTrackId
      || fullyBufferedSource.profileKey !== playbackProfile.key
    ) return
    const currentIndex = queue.items.findIndex((item) => item.id === currentQueueItemId)
    const next = currentIndex >= 0 ? queue.items[currentIndex + 1] : undefined
    if (!next) {
      audioEngine.cancelPrefetch()
      audioEngine.clearPrefetched()
      return
    }
    const url = trackAudioUrl(next.track_id, playbackProfile.key)
    playerLog("buffer", "prefetch next", { trackId: next.track_id, profile: playbackProfile.key })
    void audioEngine.prefetch(next.track_id, url, playbackProfile.key, next.id)
      .then(() => playerLog("buffer", "prefetch complete", {
        trackId: next.track_id,
        profile: playbackProfile.key,
      }))
      .catch((error: Error) => {
        if (error.name !== "AbortError") {
          playerLog("buffer", "prefetch failed", { trackId: next.track_id, message: error.message })
        }
      })
  }

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
      // Локскрин/фоновый плеер: точная позиция на системном плеере повышает
      // надёжность фонового воспроизведения и убирает «прыгающий» прогресс.
      if (
        typeof navigator !== "undefined"
        && "mediaSession" in navigator
        && "setPositionState" in navigator.mediaSession
        && Number.isFinite(duration) && duration > 0
      ) {
        try {
          navigator.mediaSession.setPositionState({
            duration,
            position: Math.min(Math.max(0, currentTime), duration),
            playbackRate: 1,
          })
        } catch {
          // невалидные значения в переходный момент — игнорируем
        }
      }
    },
    onBufferUpdate: (ranges) => get()._setBuffered(ranges),
    onFullyBuffered: (trackId, profileKey) => {
      fullyBufferedSource = { trackId, profileKey }
      playerLog("buffer", "current fully buffered", { trackId, profile: profileKey })
      scheduleNextPrefetch()
    },
    onPlaybackStateChange: (state) => get()._setPlaybackState(state),
    onEnded: () => get().handleTrackEnded(),
    onError: (message) => get()._setError(message),
  })

  audioEngine.setVolume(initVolume)
  audioEngine.setMuted(initMuted)

  // При возврате из фона дозапускаем зависший переход: мобильный браузер мог
  // заблокировать play() следующего трека в фоне (нет жеста), либо трек
  // доиграл до конца, а автопереход не стартовал. В обычном режиме мягко
  // продолжаем; в DJ-режиме сведение ручное — не вмешиваемся.
  function reconcileOnForeground() {
    const { playbackState, djEngineActive } = get()
    if (djEngineActive || playbackState !== "playing" || !audioEngine.paused) return
    const duration = audioEngine.duration
    const position = audioEngine.currentTime
    if (Number.isFinite(duration) && duration > 0 && position >= duration - 0.5) {
      // Текущий трек доиграл в фоне, но следующий не включился.
      void get().handleTrackEnded()
    } else {
      audioEngine.play().catch(() => undefined)
    }
  }

  // Точка невозврата для timeupdate — сохранить позицию сразу при уходе в фон
  // (следующий тик может уже не случиться, если вкладку выгрузят).
  if (typeof document !== "undefined") {
    const flushPosition = () => {
      if (audioEngine.currentTime > 0) persistCurrentPosition(audioEngine.currentTime)
    }
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) flushPosition()
      else reconcileOnForeground()
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

  function applyEnvelope(envelope: PlaybackEnvelope, resetHistory = false, prefetch = true) {
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
    if (prefetch) scheduleNextPrefetch()
    return { session, queue, currentItem }
  }

  async function reconcilePendingHandover() {
    const pending = pendingHandover
    if (!pending) return
    const envelope = await patchQueue(pending.sessionId, {
      operation: "handover",
      queue_item_id: pending.queueItemId,
      client_handover_id: pending.clientHandoverId,
    })
    applyEnvelope(envelope, false, false)
    await audioEngine.confirmHandover()
    pendingHandover = null
    set({ error: null })
    scheduleNextPrefetch()
  }

  async function retryPendingHandover() {
    try {
      await reconcilePendingHandover()
    } catch (err) {
      set({ error: `Handover pending: ${(err as Error).message}` })
    }
  }

  async function handoverToPrepared(next: QueueItem, session: PlaybackSession) {
    const clientHandoverId = createClientHandoverId(session.id, next.id)
    try {
      await postEvent({
        session_id: session.id,
        queue_item_id: next.id,
        track_id: next.track_id,
        event_type: "incoming_started",
        client_event_id: `incoming-${clientHandoverId}`,
      })
    } catch {
      // Semantic telemetry is best-effort; canonical handover is not.
    }
    addCurrentToHistory()
    try {
      await audioEngine.handoverPrepared(clientHandoverId)
      fullyBufferedSource = { trackId: next.track_id, profileKey: get().playbackProfile.key }
      set({
        currentTrackId: next.track_id,
        currentQueueItemId: next.id,
        currentTrack: next.track ?? null,
        currentTime: 0,
        bufferedRanges: [{ start: 0, end: 1 }],
      })
      pendingHandover = { sessionId: session.id, queueItemId: next.id, clientHandoverId }
      await reconcilePendingHandover()
      if (next.track) {
        audioEngine.setMediaSession(next.track, hiresArtworkUrl(next.track.artwork?.url, 512))
      }
    } catch (err) {
      set({ error: `Handover pending: ${(err as Error).message}` })
    }
  }

  async function advanceToNext(next: QueueItem, session: PlaybackSession) {
    if (audioEngine.hasPrepared(next.track_id, next.id)) {
      await handoverToPrepared(next, session)
      return
    }
    await get().jumpToQueueItem(next.id)
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
    bufferedRanges: [],
    volume: initVolume,
    muted: initMuted,
    error: null,
    playbackProfile: { transcodingEnabled: false, bitrateKbps: 192, key: "raw" },
    expanded: false,
    djEngineActive: false,

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

      const profile = get().playbackProfile
      const prefetchedUrl = audioEngine.consumePrefetched(trackId, profile.key)
      audioEngine.cancelPrefetch()
      if (!prefetchedUrl) audioEngine.clearPrefetched()
      fullyBufferedSource = null
      const url = prefetchedUrl ?? trackAudioUrl(trackId, profile.key)
      playerLog("buffer", prefetchedUrl ? "prefetched blob consumed" : "network source fallback", {
        trackId,
        profile: profile.key,
      })
      audioEngine.load(url, trackId, profile.key, prefetchedUrl !== null, queueItemId ?? null)
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

    async activateDj() {
      if (get().djEngineActive) return
      try {
        // Живой трек одноразово передаётся в граф с текущей позиции.
        await audioEngine.activateDjMode()
        set({ djEngineActive: true, error: null })
      } catch (err) {
        set({ error: (err as Error).message })
      }
    },

    async deactivateDj() {
      if (!get().djEngineActive) return
      try {
        // Возврат к чистому <audio> на той же позиции (разрыв при клике допустим).
        await audioEngine.deactivateDjMode()
      } catch (err) {
        set({ error: (err as Error).message })
      } finally {
        set({ djEngineActive: false })
        // Обычный prefetch пересобирает blob-кеш следующего трека.
        scheduleNextPrefetch()
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

      if (pendingHandover) {
        await retryPendingHandover()
        return
      }

      await get().recordEvent("skipped", {
        position_seconds: audioEngine.currentTime,
        duration_seconds: audioEngine.duration,
      })

      const items = queue.items
      const idx = items.findIndex((i) => i.id === currentQueueItemId)
      const next: QueueItem | undefined = items[idx + 1]
      if (next) await advanceToNext(next, session)
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

    setPlaybackProfile(profile) {
      if (profile.key === get().playbackProfile.key) return
      fullyBufferedSource = null
      audioEngine.cancelPrefetch()
      set({ playbackProfile: profile })
      playerLog("buffer", "playback profile changed", { profile: profile.key })
    },

    toggleMute() {
      const muted = !get().muted
      playerLog("volume", "toggleMute", { muted, volume: get().volume })
      audioEngine.setMuted(muted)
      set({ muted })
      persistVolume(get().volume, muted)
    },

    async refreshQueue() {
      if (pendingHandover) {
        try {
          await reconcilePendingHandover()
        } catch (err) {
          set({ error: `Handover pending: ${(err as Error).message}` })
        }
        return
      }
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
      const { session, queue, currentQueueItemId, djEngineActive } = get()

      addCurrentToHistory()
      // The backend intentionally keeps the completed item as current. Do not
      // resurrect its near-duration position if the tab is discarded now.
      throttledPersistPosition.cancel()
      clearPersistedPlaybackPosition()

      // DJ-движок активен → ручное сведение: дека останавливается в конечной
      // позиции, статус переходит в паузу, автоперехода нет.
      if (djEngineActive) {
        set({ playbackState: "paused" })
        void get().recordEvent("completed", {
          position_seconds: audioEngine.duration,
          duration_seconds: audioEngine.duration,
          play_fraction: 1,
        })
        return
      }

      // Телеметрию «completed» шлём НЕ блокируя переход. Awaited postEvent в фоне
      // может зависнуть (фоновый fetch троттлится), и именно это раньше не давало
      // следующему треку стартовать. recordEvent читает текущее состояние, поэтому
      // вызываем до перевода указателей на следующий трек.
      void get().recordEvent("completed", {
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

      // Queue exhausted. recordEvent обычно уже дёрнул refill, но если postEvent
      // упал — подстрахуемся явно (guard refillInFlight не даст дубля).
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

    async adoptInstantMix(envelope) {
      const { currentTrackId: playingTrackId, playbackState } = get()
      const canPreservePlayback = playbackState === "playing"
        || playbackState === "paused"
        || playbackState === "loading"
      const matchingItem = playingTrackId == null
        ? null
        : envelope.queue.items.find((item) => item.track_id === playingTrackId) ?? null

      const preservedItem = canPreservePlayback ? matchingItem : null
      // Instant Mix for the currently playing track replaces only its listening
      // context. Reusing the loaded audio keeps the exact playback position and
      // paused/playing state instead of restarting the file from zero.
      if (!preservedItem) {
        await get().playFromEnvelope(envelope)
        return
      }

      set({ error: null })
      try {
        let adopted = envelope
        if (envelope.session.current_queue_item_id !== preservedItem.id) {
          adopted = await patchSession(envelope.session.id, {
            current_track_id: preservedItem.track_id,
            current_queue_item_id: preservedItem.id,
          })
        }
        applyEnvelope(adopted, true)
        persistSessionId(adopted.session.id)
        scheduleAutoplayRefill()
      } catch (err) {
        set({ error: (err as Error).message })
      }
    },

    async playNext(trackId, sourceLabel) {
      const { session, queue, currentQueueItemId } = get()
      if (!session?.id || !queue || !currentQueueItemId) {
        await get().playSource("track", trackId, sourceLabel ?? String(trackId))
        return
      }

      try {
        const existingIds = new Set(queue.items.map((item) => item.id))
        const added = await patchQueue(session.id, { operation: "add", track_id: trackId })
        const addedItem = added.queue.items.find((item) => !existingIds.has(item.id))
        const currentIndex = added.queue.items.findIndex((item) => item.id === currentQueueItemId)

        if (!addedItem || currentIndex < 0) {
          applyEnvelope(added)
          return
        }

        const targetPosition = currentIndex + 1
        if (added.queue.items[targetPosition]?.id === addedItem.id) {
          applyEnvelope(added)
          return
        }

        const moved = await patchQueue(session.id, {
          operation: "move",
          queue_item_id: addedItem.id,
          position: targetPosition,
        })
        applyEnvelope(moved)
      } catch (err) {
        set({ error: (err as Error).message })
      }
    },

    async prepareDjDeck(trackId, deck) {
      const { session, queue, currentQueueItemId, playbackProfile } = get()
      const snapshot = audioEngine.getEngineSnapshot()
      if (snapshot.programDeck === deck || snapshot.decks[deck].transport === "playing") {
        set({ error: `Deck ${deck} is currently on air` })
        return
      }
      if (!session?.id || !queue || !currentQueueItemId) {
        set({ error: "Start a playback session before loading a DJ deck" })
        return
      }

      try {
        const currentIndex = queue.items.findIndex((item) => item.id === currentQueueItemId)
        if (currentIndex < 0) throw new Error("Current queue item is unavailable")
        let envelope: PlaybackEnvelope
        let target = queue.items.slice(currentIndex + 1).find((item) => item.track_id === trackId)

        if (target) {
          envelope = { session, queue } as PlaybackEnvelope
        } else {
          const existingIds = new Set(queue.items.map((item) => item.id))
          envelope = await patchQueue(session.id, { operation: "add", track_id: trackId })
          target = envelope.queue.items.find((item) => !existingIds.has(item.id))
        }
        if (!target) throw new Error("Could not add the track to the playback queue")

        const targetPosition = currentIndex + 1
        if (envelope.queue.items[targetPosition]?.id !== target.id) {
          envelope = await patchQueue(session.id, {
            operation: "move",
            queue_item_id: target.id,
            position: targetPosition,
          })
          target = envelope.queue.items.find((item) => item.id === target?.id) ?? target
        }

        applyEnvelope(envelope, false, false)
        if (!audioEngine.hasPrepared(trackId, target.id)) {
          await audioEngine.prepareDjDeck(
            trackId,
            trackAudioUrl(trackId, playbackProfile.key),
            playbackProfile.key,
            target.id,
          )
        }
        set({ error: null })
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
        const { currentTrackId, currentTrack, currentQueueItemId: restoreQueueItemId } = get()
        if (currentTrackId) {
          const profile = get().playbackProfile
          audioEngine.load(
            trackAudioUrl(currentTrackId, profile.key),
            currentTrackId,
            profile.key,
            false,
            restoreQueueItemId,
          )
          audioEngine.setVolume(get().volume)
          audioEngine.setMuted(get().muted)
          // Вернуть сохранённую позицию — сработает, когда пользователь
          // нажмёт play (метаданные при preload="none" грузятся только тогда).
          const persisted = loadPersistedPlaybackPosition()
          const sessionId = get().session?.id
          const queueItemId = restoreQueueItemId
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

    resetForLogout() {
      throttledSetTime.cancel()
      throttledPersistPosition.cancel()
      refillInFlight = false
      fullyBufferedSource = null
      pendingHandover = null
      incomingStartedQueueItems.clear()
      audioEngine.clear()
      set({
        session: null,
        queue: null,
        playedHistory: [],
        currentTrackId: null,
        currentQueueItemId: null,
        currentTrack: null,
        playbackState: "idle",
        currentTime: 0,
        duration: 0,
        bufferedRanges: [],
        error: null,
        expanded: false,
        djEngineActive: false,
      })
    },

    toggleExpanded() {
      useUIStore.getState().closeDjSurface()
      set((s) => ({ expanded: !s.expanded }))
    },

    async toggleDjDeck(deck) {
      const before = audioEngine.getEngineSnapshot()
      const target = before.decks[deck]
      try {
        await audioEngine.toggleDeck(deck)
      } catch (err) {
        set({ error: (err as Error).message })
        return
      }
      if (
        deck !== before.programDeck
        && target.transport !== "playing"
        && target.trackId !== null
        && target.queueItemId !== null
        && !incomingStartedQueueItems.has(target.queueItemId)
      ) {
        incomingStartedQueueItems.add(target.queueItemId)
        const { session } = get()
        try {
          await postEvent({
            session_id: session?.id ?? "",
            queue_item_id: target.queueItemId,
            track_id: target.trackId,
            event_type: "incoming_started",
          })
        } catch {
          // Incoming playback is browser-local; semantic reporting is best-effort.
          incomingStartedQueueItems.delete(target.queueItemId)
        }
      }
    },

    // --- Internal callbacks from AudioEngine ---
    _setTime(currentTime, duration) {
      set({ currentTime, duration })
    },
    _setBuffered(ranges) {
      set({ bufferedRanges: ranges })
    },
    _setPlaybackState(state) {
      set({ playbackState: state })
      // Системный медиа-плеер (локскрин/шторка): без явного playbackState
      // некоторые браузеры не удерживают фоновую сессию. "loading" держим как
      // "playing", чтобы статус не мигал в момент автоперехода между треками.
      if (typeof navigator !== "undefined" && "mediaSession" in navigator) {
        navigator.mediaSession.playbackState = state === "paused"
          ? "paused"
          : state === "idle" || state === "error"
            ? "none"
            : "playing"
      }
    },
    _setError(message) {
      set({ error: message, playbackState: "error" })
    },
  }
})
