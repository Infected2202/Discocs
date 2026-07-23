import type { TrackSummary } from "@/api/types"
import { playerLog } from "@/lib/playerLogger"
import { PlaybackEngine } from "./PlaybackEngine"
import type {
  DeckId,
  DeckSourceUpgradeResult,
  EqBand,
  HandoverResult,
  PlaybackEngineSnapshot,
  SyncMode,
  TempoNudgeDirection,
  TrackSource,
  TransportState,
} from "./types"

export type PlaybackState = "idle" | "loading" | "playing" | "paused" | "error"
export interface BufferedRange {
  start: number
  end: number
}

export interface NextTrackBufferInfo {
  trackId: number
  queueItemId: string | null
  ready: boolean
}

interface AudioEngineCallbacks {
  onTimeUpdate(currentTime: number, duration: number): void
  onPlaybackStateChange(state: PlaybackState): void
  onBufferUpdate(ranges: BufferedRange[]): void
  onFullyBuffered?(trackId: number, profileKey: string): void
  onSeekBufferingChange?(active: boolean): void
  onNextTrackBufferingChange?(info: NextTrackBufferInfo | null): void
  onEnded(): void
  onError(message: string): void
}

export class PlayerPlaybackFacade {
  private el: HTMLAudioElement
  private readonly runtime: PlaybackEngine
  private callbacks: AudioEngineCallbacks | null = null
  private activeTrackId: number | null = null
  private activeQueueItemId: string | null = null
  private activeProfileKey = "raw"
  private activeNetworkUrl: string | null = null
  private pendingNetworkSeek: { fraction: number; wasPlaying: boolean } | null = null
  private activeObjectUrl: string | null = null
  private activeCacheController: AbortController | null = null
  private activeCacheTarget: { trackId: number; profileKey: string; url: string } | null = null
  private activeCacheRetryCount = 0
  private fullyBufferedReported = false
  private prefetched: {
    trackId: number
    queueItemId: string | null
    profileKey: string
    objectUrl: string
    blob: Blob
  } | null = null
  private prefetchRetryCount = 0
  // DJ deck: a fully isolated resource populated only by prepareDjDeck(),
  // never by ordinary background prefetch. The only allowed handoff is the
  // one-time seed from `prefetched` at DJ-mode activation (see
  // ensurePreparedDeckFromCache) — after that instant the two never alias.
  private djDeck: {
    trackId: number
    queueItemId: string | null
    profileKey: string
    objectUrl: string
    blob: Blob
    element: HTMLAudioElement
    deck: DeckId
  } | null = null
  private djDeckController: AbortController | null = null
  private djDeckTarget: { trackId: number; profileKey: string; queueItemId: string | null } | null = null
  private retired: { element: HTMLAudioElement; objectUrl: string | null; blob: Blob | null; deck: DeckId } | null = null
  private prefetchController: AbortController | null = null
  private prefetchTarget: { trackId: number; profileKey: string; queueItemId: string | null } | null = null
  // False = ordinary direct-<audio> playback (no AudioContext, reliable mobile
  // background). True = DJ mode: audio routed through the Web Audio mixer graph.
  // The graph is only ever created once DJ mode is activated by a user gesture.
  private graphActive = false
  private activeBlob: Blob | null = null
  private readonly upgradePromises: Record<DeckId, Promise<DeckSourceUpgradeResult> | null> = { A: null, B: null }
  private djActivationPromise: Promise<void> | null = null
  private djDeactivationPromise: Promise<void> | null = null
  private runtimeUnsubscribe: (() => void) | null = null
  private lastRuntimeTransport: TransportState | null = null

  constructor(runtime = new PlaybackEngine()) {
    this.runtime = runtime
    this.el = this.createElement()
  }

  init(callbacks: AudioEngineCallbacks) {
    this.callbacks = callbacks
    if (!this.runtimeUnsubscribe && typeof this.runtime.subscribe === "function") {
      this.runtimeUnsubscribe = this.runtime.subscribe(() => this.syncRuntimeCallbacks())
    }
  }

  load(
    url: string,
    trackId: number | null = null,
    profileKey = "raw",
    fullyAvailable = false,
    queueItemId: string | null = null,
  ) {
    this.cancelActiveCache()
    if (this.pendingNetworkSeek) this.callbacks?.onSeekBufferingChange?.(false)
    this.pendingNetworkSeek = null
    this.activeCacheRetryCount = 0
    const retainedBlob = fullyAvailable && this.activeObjectUrl === url ? this.activeBlob : null
    // Явно освобождаем буфер старого элемента — src='' надёжнее removeAttribute
    const prev = this.el
    prev.pause()
    prev.src = ""
    prev.load()
    if (this.activeObjectUrl && this.activeObjectUrl !== url) {
      URL.revokeObjectURL(this.activeObjectUrl)
      this.activeObjectUrl = null
    }
    this.activeBlob = retainedBlob

    // Новый элемент — Chrome гарантированно освобождает нативный PCM буфер
    // когда старый элемент теряет все ссылки и GC его собирает
    this.el = this.createElement()
    // Обычный режим (graphActive=false) НЕ заводит элемент в Web Audio: чистый
    // <audio> надёжно играет в фоне/на локскрине, тогда как элемент, пропущенный
    // через AudioContext, зависает вместе с суспендом контекста на мобиле.
    // В граф элемент попадает только при активации DJ (activateDjMode).
    if (this.graphActive) this.runtime.routeProgramElement(this.el, trackId, queueItemId)
    this.el.volume = prev.volume
    this.el.muted = prev.muted
    this.el.src = url
    this.el.load()
    this.activeTrackId = trackId
    this.activeQueueItemId = queueItemId
    this.activeProfileKey = profileKey
    this.activeNetworkUrl = fullyAvailable || url.startsWith("blob:") ? null : url
    this.fullyBufferedReported = false
    this.lastRuntimeTransport = null

    // Reset immediately — otherwise the buffered indicator briefly shows
    // the previous track's ranges. A prepared Blob is already fully local.
    this.callbacks?.onBufferUpdate(fullyAvailable ? [{ start: 0, end: 1 }] : [])
    if (fullyAvailable && trackId !== null) this.reportFullyBuffered()
    if (this.graphActive && trackId !== null) {
      this.queueStretchUpgrade(this.runtime.programDeck, {
        url,
        trackId,
        queueItemId,
        blob: this.activeBlob ?? undefined,
      })
    }
  }

  async prefetch(trackId: number, url: string, profileKey: string, queueItemId: string | null = null): Promise<void> {
    // Dedup on trackId+profileKey alone — queueItemId is just the queue-row
    // label the caller currently has in mind. A queue resync (e.g. the
    // background PATCH sync after an optimistic jump) can hand out a fresh
    // queue_item_id for the same still-upcoming track; treating that as a
    // "new" target used to discard an already-downloaded/in-flight Blob and
    // refetch the identical audio from scratch.
    if (this.prefetched?.trackId === trackId && this.prefetched.profileKey === profileKey) {
      if (this.prefetched.queueItemId !== queueItemId) {
        this.prefetched = { ...this.prefetched, queueItemId }
        this.callbacks?.onNextTrackBufferingChange?.({ trackId, queueItemId, ready: true })
      }
      return
    }
    if (this.prefetchTarget?.trackId === trackId && this.prefetchTarget.profileKey === profileKey) {
      this.prefetchTarget.queueItemId = queueItemId
      return
    }
    this.cancelPrefetch()
    this.clearPrefetched()
    this.prefetchTarget = { trackId, profileKey, queueItemId }
    this.prefetchRetryCount = 0
    this.callbacks?.onNextTrackBufferingChange?.({ trackId, queueItemId, ready: false })

    try {
      for (;;) {
        const controller = new AbortController()
        this.prefetchController = controller
        try {
          const response = await fetch(url, {
            credentials: "same-origin",
            signal: controller.signal,
          })
          if (!response.ok) throw new Error(`Audio prefetch failed: HTTP ${response.status}`)
          const blob = await response.blob()
          if (controller.signal.aborted) return
          if (this.prefetchTarget?.trackId !== trackId || this.prefetchTarget.profileKey !== profileKey) return
          const objectUrl = URL.createObjectURL(blob)
          // Graph-unaware by design: prefetch() only ever populates the ordinary
          // blob/objectUrl cache, regardless of DJ-mode state. It never routes an
          // element into the mixer graph — that is prepareDjDeck()'s job alone,
          // so routine background caching can never delete an armed DJ deck.
          // Use the target's current queueItemId, not the closure param — a
          // superseding call may have relabelled it while this fetch was in flight.
          const resolvedQueueItemId = this.prefetchTarget.queueItemId
          this.prefetched = { trackId, queueItemId: resolvedQueueItemId, profileKey, objectUrl, blob }
          this.callbacks?.onNextTrackBufferingChange?.({ trackId, queueItemId: resolvedQueueItemId, ready: true })
          return
        } catch (error) {
          const err = error as Error
          if (err.name === "AbortError" || controller.signal.aborted) return
          if (this.prefetchTarget?.trackId !== trackId || this.prefetchTarget.profileKey !== profileKey) return
          if (this.prefetchRetryCount < 1) {
            this.prefetchRetryCount += 1
            playerLog("buffer", "prefetch retry", { trackId, profile: profileKey })
            continue
          }
          throw err
        }
      }
    } finally {
      if (this.prefetchTarget?.trackId === trackId && this.prefetchTarget.profileKey === profileKey) {
        this.prefetchController = null
        this.prefetchTarget = null
      }
    }
  }

  consumePrefetched(trackId: number, profileKey: string): string | null {
    if (this.prefetched?.trackId !== trackId || this.prefetched.profileKey !== profileKey) return null
    const objectUrl = this.prefetched.objectUrl
    this.activeBlob = this.prefetched.blob
    this.prefetched = null
    this.callbacks?.onNextTrackBufferingChange?.(null)
    if (this.activeObjectUrl && this.activeObjectUrl !== objectUrl) {
      URL.revokeObjectURL(this.activeObjectUrl)
    }
    this.activeObjectUrl = objectUrl
    return objectUrl
  }

  cancelPrefetch() {
    const hadTarget = this.prefetchController !== null || this.prefetchTarget !== null
    this.prefetchController?.abort()
    this.prefetchController = null
    this.prefetchTarget = null
    if (hadTarget) this.callbacks?.onNextTrackBufferingChange?.(null)
  }

  clearPrefetched() {
    // Only the ordinary blob cache — never touches the DJ deck. Background
    // prefetch (which calls this via prefetch()/directly) must never tear
    // down a manually-armed DJ deck as a side effect.
    if (this.prefetched) {
      URL.revokeObjectURL(this.prefetched.objectUrl)
      this.prefetched = null
      this.callbacks?.onNextTrackBufferingChange?.(null)
    }
  }

  /**
   * Load a track onto the DJ deck (the isolated resource used by the DJ's
   * manual "load onto second deck" action) — own fetch, own element, own
   * AbortController. Never touches `prefetched`/`prefetchController`, so
   * ordinary background prefetch can never observe or disturb it.
   */
  async prepareDjDeck(trackId: number, url: string, profileKey: string, queueItemId: string | null = null): Promise<void> {
    if (
      this.djDeck?.trackId === trackId
      && this.djDeck.profileKey === profileKey
      && this.djDeck.queueItemId === queueItemId
    ) return
    this.clearDjDeck()
    const controller = new AbortController()
    this.djDeckController = controller
    this.djDeckTarget = { trackId, profileKey, queueItemId }
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        signal: controller.signal,
      })
      if (!response.ok) throw new Error(`DJ deck load failed: HTTP ${response.status}`)
      const blob = await response.blob()
      if (controller.signal.aborted) return
      if (
        this.djDeckTarget?.trackId !== trackId
        || this.djDeckTarget.profileKey !== profileKey
        || this.djDeckTarget.queueItemId !== queueItemId
      ) return
      const objectUrl = URL.createObjectURL(blob)
      const element = this.createElement()
      element.volume = this.el.volume
      element.muted = this.el.muted
      element.src = objectUrl
      element.load()
      const deck = this.runtime.routeIncomingElement(element, trackId, queueItemId)
      if (deck) {
        this.djDeck = { trackId, queueItemId, profileKey, objectUrl, blob, element, deck }
        await this.queueStretchUpgrade(deck, { url: objectUrl, trackId, queueItemId, blob })
      } else {
        element.src = ""
        element.load()
      }
    } finally {
      if (this.djDeckController === controller) {
        this.djDeckController = null
        this.djDeckTarget = null
      }
    }
  }

  /** Tear down the DJ deck alone — never reaches into `prefetched`. */
  clearDjDeck(): void {
    this.djDeckController?.abort()
    this.djDeckController = null
    this.djDeckTarget = null
    if (this.djDeck) {
      this.runtime.cancelIncoming()
      this.djDeck.element.pause()
      this.djDeck.element.src = ""
      this.djDeck.element.load()
      this.djDeck = null
    }
  }

  hasPrepared(trackId: number, queueItemId: string): boolean {
    return this.djDeck?.trackId === trackId
      && this.djDeck.queueItemId === queueItemId
  }

  async handoverPrepared(clientHandoverId: string): Promise<{
    trackId: number
    queueItemId: string
    profileKey: string
    outgoingDeck: DeckId
    programDeck: DeckId
  }> {
    const incoming = this.djDeck
    if (!incoming?.queueItemId) throw new Error("Incoming deck is not prepared")
    await this.runtime.ensureReady()
    const previous = this.el
    const previousObjectUrl = this.activeObjectUrl
    const previousBlob = this.activeBlob
    this.cancelActiveCache()
    if (this.runtime.isStretchDeck(incoming.deck)) await this.runtime.playDeck(incoming.deck)
    else await incoming.element.play()
    let result: HandoverResult
    try {
      result = await this.runtime.handover({
        incomingDeck: incoming.deck,
        clientHandoverId,
      })
    } catch (error) {
      if (this.runtime.isStretchDeck(incoming.deck)) await this.runtime.pauseDeck(incoming.deck)
      else incoming.element.pause()
      throw error
    }
    this.el = incoming.element
    this.retired = { element: previous, objectUrl: previousObjectUrl, blob: previousBlob, deck: result.outgoingDeck }
    this.activeTrackId = incoming.trackId
    this.activeQueueItemId = incoming.queueItemId
    this.activeProfileKey = incoming.profileKey
    this.activeNetworkUrl = null
    this.activeObjectUrl = incoming.objectUrl
    this.activeBlob = incoming.blob
    this.fullyBufferedReported = true
    this.prefetched = null
    this.djDeck = null
    this.callbacks?.onBufferUpdate([{ start: 0, end: 1 }])
    this.callbacks?.onPlaybackStateChange("playing")
    return {
      trackId: incoming.trackId,
      queueItemId: incoming.queueItemId,
      profileKey: incoming.profileKey,
      outgoingDeck: result.outgoingDeck,
      programDeck: result.programDeck,
    }
  }

  async confirmHandover(): Promise<void> {
    const retired = this.retired
    if (!retired) return
    this.retired = null
    retired.element.pause()
    retired.element.src = ""
    retired.element.load()
    if (retired.objectUrl && retired.objectUrl !== this.activeObjectUrl) {
      URL.revokeObjectURL(retired.objectUrl)
    }
    retired.blob = null
    await this.runtime.confirmRetirement(retired.deck)
  }

  activateDjMode(): Promise<void> {
    if (this.djActivationPromise) return this.djActivationPromise
    if (this.graphActive) return Promise.resolve()
    const pending = this.activateDjModeInternal()
    this.djActivationPromise = pending
    void pending.catch(() => {
      if (this.djActivationPromise === pending) this.djActivationPromise = null
    })
    return pending
  }

  private async activateDjModeInternal(): Promise<void> {
    await this.runtime.ensureReady()
    this.graphActive = true
    this.runtime.routeProgramElement(this.el, this.activeTrackId, this.activeQueueItemId)
    this.runtime.setMasterGain(this.el.muted ? 0 : this.el.volume)

    const trackId = this.activeTrackId
    if (trackId !== null) {
      const element = this.el
      const sourceUrl = this.activeObjectUrl ?? this.activeNetworkUrl ?? element.currentSrc ?? element.src
      const result = await this.queueStretchUpgrade(this.runtime.programDeck, {
        url: sourceUrl,
        trackId,
        queueItemId: this.activeQueueItemId,
        blob: this.activeBlob ?? undefined,
      }, {
        startAtSeconds: element.currentTime,
        autoplay: !element.paused,
      })
      if (result.upgraded && element === this.el) {
        element.pause()
        this.activeNetworkUrl = null
        this.reportFullyBuffered()
      }
    }

    // Обычный prefetch кеширует только blob — при активации достраиваем из него
    // incoming-деку в графе.
    this.ensurePreparedDeckFromCache()
    const incoming = this.djDeck
    if (incoming) {
      await this.queueStretchUpgrade(incoming.deck, {
        url: incoming.objectUrl,
        trackId: incoming.trackId,
        queueItemId: incoming.queueItemId,
        blob: incoming.blob,
      })
    }
  }

  private ensurePreparedDeckFromCache(): void {
    // The one explicitly-allowed handoff (product decision #3): seed the DJ
    // deck once, at activation, from whatever the ordinary player already
    // has cached — no new network fetch. After this instant `djDeck` and
    // `prefetched` never alias again.
    if (this.djDeck || !this.prefetched) return
    const { trackId, queueItemId, profileKey, objectUrl, blob } = this.prefetched
    const element = this.createElement()
    element.volume = this.el.volume
    element.muted = this.el.muted
    element.src = objectUrl
    element.load()
    const deck = this.runtime.routeIncomingElement(element, trackId, queueItemId)
    if (deck) {
      this.djDeck = { trackId, queueItemId, profileKey, objectUrl, blob, element, deck }
    } else {
      element.src = ""
      element.load()
    }
  }

  deactivateDjMode(): Promise<void> {
    if (this.djDeactivationPromise) return this.djDeactivationPromise
    if (!this.graphActive) return Promise.resolve()
    const pending = this.deactivateDjModeInternal()
    this.djDeactivationPromise = pending
    const clearPending = () => {
      if (this.djDeactivationPromise === pending) this.djDeactivationPromise = null
    }
    void pending.then(clearPending, clearPending)
    return pending
  }

  private async deactivateDjModeInternal(): Promise<void> {
    // Дождаться незавершённой активации, чтобы разбирать полностью собранный граф.
    await this.djActivationPromise?.catch(() => undefined)

    // Позицию и статус читаем ДО destroy — currentTime/paused опрашивают рантайм.
    const previous = this.el
    const position = this.currentTime
    const wasPlaying = !this.paused
    // blob-трек, загруженный напрямую, не оседает в activeObjectUrl — тогда берём
    // источник из самого элемента.
    const sourceUrl = this.activeObjectUrl ?? this.activeNetworkUrl ?? previous.currentSrc ?? previous.src

    // Свежий, НЕ заведённый в граф <audio> на той же позиции. При клике разрыв
    // допустим: createMediaElementSource необратим, поэтому нужен новый элемент.
    const next = this.createElement()
    next.volume = previous.volume
    next.muted = previous.muted
    this.el = next
    this.graphActive = false
    this.djActivationPromise = null
    this.lastRuntimeTransport = null
    this.upgradePromises.A = null
    this.upgradePromises.B = null

    if (sourceUrl) {
      next.src = sourceUrl
      const resume = () => {
        next.removeEventListener("loadedmetadata", resume)
        if (next !== this.el) return
        next.currentTime = Math.min(position, next.duration || position)
        if (wasPlaying) {
          void next.play().catch((error: Error) => {
            this.callbacks?.onPlaybackStateChange("error")
            this.callbacks?.onError(error.message)
          })
        }
      }
      next.addEventListener("loadedmetadata", resume)
      next.load()
    }

    previous.pause()
    previous.src = ""
    previous.load()

    // Деки графа больше не нужны — обычный prefetch пересоберёт blob-кеш.
    if (this.djDeck) {
      this.djDeck.element.pause()
      this.djDeck.element.src = ""
      this.djDeck.element.load()
      this.djDeck = null
    }
    if (this.retired) {
      this.retired.element.pause()
      this.retired.element.src = ""
      this.retired.element.load()
      if (this.retired.objectUrl && this.retired.objectUrl !== this.activeObjectUrl) {
        URL.revokeObjectURL(this.retired.objectUrl)
      }
      this.retired = null
    }

    await this.runtime.destroy()
    this.callbacks?.onPlaybackStateChange(wasPlaying ? "playing" : "paused")
  }

  get djModeActive(): boolean {
    return this.graphActive
  }

  async play(): Promise<void> {
    if (this.pendingNetworkSeek) this.pendingNetworkSeek.wasPlaying = true
    // Обычный режим не трогает AudioContext — иначе создание/резюм контекста
    // снова привязывает воспроизведение к суспендируемому в фоне графу.
    if (this.graphActive) await this.runtime.ensureReady()
    const deck = this.runtime.programDeck
    await this.upgradePromises[deck]
    if (this.runtime.isStretchDeck(deck)) {
      await this.runtime.playDeck(deck)
      this.reportFullyBuffered()
    } else {
      await this.el.play()
    }
    // `preload=auto` is only a browser hint and commonly stalls around 90%.
    // Once playback has actually started, explicitly consume the complete
    // response into a Blob. Native playback remains uninterrupted; the Blob
    // guarantees that all bytes are present before the next prefetch begins.
    if (!this.runtime.isStretchDeck(deck)) this.cacheActiveTrack()
  }

  pause() {
    if (this.pendingNetworkSeek) this.pendingNetworkSeek.wasPlaying = false
    const deck = this.runtime.programDeck
    if (this.runtime.isStretchDeck(deck)) {
      void this.runtime.pauseDeck(deck).catch((error: Error) => this.callbacks?.onError(error.message))
    } else {
      this.el.pause()
    }
  }

  clear() {
    const prev = this.el
    const { volume, muted } = prev
    prev.pause()
    prev.src = ""
    prev.load()
    this.cancelPrefetch()
    this.clearPrefetched()
    this.clearDjDeck()
    this.cancelActiveCache()
    if (this.activeObjectUrl) URL.revokeObjectURL(this.activeObjectUrl)
    this.activeObjectUrl = null
    this.activeBlob = null
    this.activeNetworkUrl = null
    if (this.pendingNetworkSeek) this.callbacks?.onSeekBufferingChange?.(false)
    this.pendingNetworkSeek = null
    this.activeCacheRetryCount = 0
    this.activeTrackId = null
    this.activeQueueItemId = null
    this.fullyBufferedReported = false
    this.graphActive = false
    this.djActivationPromise = null
    this.djDeactivationPromise = null
    this.lastRuntimeTransport = null
    this.upgradePromises.A = null
    this.upgradePromises.B = null
    if (this.retired) {
      this.retired.element.pause()
      this.retired.element.src = ""
      this.retired.element.load()
      if (this.retired.objectUrl) URL.revokeObjectURL(this.retired.objectUrl)
      this.retired = null
    }

    this.el = this.createElement()
    void this.runtime.destroy().catch((error: Error) => {
      playerLog("engine", "destroy failed", { message: error.message })
    })
    this.el.volume = volume
    this.el.muted = muted

    this.callbacks?.onTimeUpdate(0, 0)
    this.callbacks?.onBufferUpdate([])
    this.callbacks?.onPlaybackStateChange("idle")

    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = null
      for (const action of ["play", "pause", "nexttrack", "previoustrack"] as MediaSessionAction[]) {
        try {
          navigator.mediaSession.setActionHandler(action, null)
        } catch {
          // browser may not support all actions
        }
      }
    }
  }

  seek(fraction: number) {
    if (!Number.isFinite(fraction)) return
    const clamped = Math.min(1, Math.max(0, fraction))
    const deck = this.runtime.programDeck
    const snapshot = this.runtime.getSnapshot().decks[deck]
    if (snapshot.sourceKind === "signalsmith" && snapshot.duration) {
      this.pendingNetworkSeek = null
      void this.runtime.seekDeck(deck, clamped * snapshot.duration)
        .catch((error: Error) => this.callbacks?.onError(error.message))
      return
    }
    const el = this.el
    if (this.activeNetworkUrl) {
      // The raw network stream is not reliably seekable while still being
      // transcoded upstream: writing el.currentTime here can be silently
      // accepted and then reset to 0, which is audible as the track
      // restarting. Instead, pause and wait for the full-track Blob swap
      // (activateCachedSource) to apply the position on a genuinely local,
      // always-seekable source.
      const wasPlaying = this.pendingNetworkSeek?.wasPlaying ?? !el.paused
      this.pendingNetworkSeek = { fraction: clamped, wasPlaying }
      el.pause()
      this.callbacks?.onSeekBufferingChange?.(true)
      this.cacheActiveTrack()
      return
    }
    this.pendingNetworkSeek = null
    const apply = () => {
      el.removeEventListener("loadedmetadata", apply)
      if (el !== this.el || !Number.isFinite(el.duration) || el.duration <= 0) return
      el.currentTime = clamped * el.duration
    }
    if (Number.isFinite(el.duration) && el.duration > 0) apply()
    else el.addEventListener("loadedmetadata", apply)
  }

  seekToSeconds(seconds: number) {
    const deck = this.runtime.programDeck
    if (this.runtime.isStretchDeck(deck)) {
      void this.runtime.seekDeck(deck, seconds).catch((error: Error) => this.callbacks?.onError(error.message))
    } else {
      this.el.currentTime = seconds
    }
  }

  seekDeckToSeconds(deck: DeckId, seconds: number): void {
    if (this.runtime.isStretchDeck(deck)) {
      void this.runtime.seekDeck(deck, seconds).catch((error: Error) => this.callbacks?.onError(error.message))
      return
    }
    const element = this.elementForDeck(deck)
    if (!element || !Number.isFinite(seconds)) return
    const maximum = Number.isFinite(element.duration) && element.duration > 0
      ? element.duration
      : Number.POSITIVE_INFINITY
    element.currentTime = Math.min(Math.max(0, seconds), maximum)
  }

  /**
   * Seek as soon as the track's metadata is available. Usable right after
   * load(): duration can remain unknown until metadata arrives,
   * so an immediate currentTime write would be silently dropped.
   */
  resumeAtSeconds(seconds: number) {
    const deck = this.runtime.programDeck
    if (this.runtime.isStretchDeck(deck)) {
      void this.runtime.seekDeck(deck, seconds).catch((error: Error) => this.callbacks?.onError(error.message))
      return
    }
    if (Number.isFinite(this.el.duration) && this.el.duration > 0) {
      this.el.currentTime = seconds
      return
    }
    const el = this.el
    const apply = () => {
      el.removeEventListener("loadedmetadata", apply)
      el.currentTime = seconds
    }
    el.addEventListener("loadedmetadata", apply)
  }

  setVolume(v: number) {
    const volume = Math.max(0, Math.min(1, v))
    this.el.volume = volume
    if (this.djDeck) this.djDeck.element.volume = volume
    if (this.graphActive) this.runtime.setMasterGain(this.el.muted ? 0 : volume)
  }

  setMuted(muted: boolean) {
    this.el.muted = muted
    if (this.djDeck) this.djDeck.element.muted = muted
    if (this.graphActive) this.runtime.setMasterGain(muted ? 0 : this.el.volume)
  }

  get currentTime() {
    const deck = this.runtime.programDeck
    const snapshot = this.runtime.getSnapshot().decks[deck]
    return snapshot.sourceKind === "signalsmith"
      ? snapshot.anchor?.mediaSeconds ?? 0
      : this.el.currentTime
  }

  get duration() {
    const deck = this.runtime.programDeck
    const snapshot = this.runtime.getSnapshot().decks[deck]
    return snapshot.sourceKind === "signalsmith"
      ? snapshot.duration ?? 0
      : this.el.duration
  }

  get paused() {
    const deck = this.runtime.programDeck
    const snapshot = this.runtime.getSnapshot().decks[deck]
    return snapshot.sourceKind === "signalsmith"
      ? snapshot.transport !== "playing"
      : this.el.paused
  }

  getEngineSnapshot(): PlaybackEngineSnapshot {
    return this.runtime.getSnapshot()
  }

  getDeckCurrentTime(deck: DeckId): number | null {
    const snapshot = this.runtime.getSnapshot().decks[deck]
    if (snapshot.sourceKind === "signalsmith") return snapshot.anchor?.mediaSeconds ?? null
    const element = this.elementForDeck(deck)
    return element && Number.isFinite(element.currentTime) ? element.currentTime : null
  }

  getMixerMeters(): Record<DeckId | "master", number> {
    return this.runtime.getMeterLevels()
  }

  subscribeEngine(listener: () => void): () => void {
    return this.runtime.subscribe(listener)
  }

  setDeckTrim(deck: DeckId, value: number): void {
    this.runtime.setTrim(deck, value)
  }

  setDeckEq(deck: DeckId, band: EqBand, value: number): void {
    this.runtime.setEq(deck, band, value)
  }

  setDeckFilter(deck: DeckId, value: number): void {
    this.runtime.setFilter(deck, value)
  }

  setDeckChannelFader(deck: DeckId, value: number): void {
    this.runtime.setChannelFader(deck, value)
  }

  setCrossfader(value: number): void {
    this.runtime.setCrossfader(value)
  }

  setMasterGain(value: number): void {
    this.runtime.setMasterGain(value)
  }

  setDeckTempo(deck: DeckId, ratio: number): Promise<void> {
    return this.runtime.setTempo(deck, ratio)
  }

  setAutoTempoMaster(): Promise<void> {
    return this.runtime.setAutoMaster()
  }

  setClockTempoMaster(): Promise<void> {
    return this.runtime.setClockMaster()
  }

  async setDeckTempoMaster(deck: DeckId): Promise<void> {
    await this.djActivationPromise
    await this.ensureStretchDeck(deck)
    const snapshot = this.runtime.getSnapshot()
    await Promise.all((["A", "B"] as const)
      .filter((candidate) => candidate !== deck && snapshot.tempoSync.decks[candidate].enabled)
      .map((candidate) => this.ensureStretchDeck(candidate)))
    return this.runtime.setTempoMaster(deck)
  }

  setMasterClockTempo(bpm: number): Promise<void> {
    return this.runtime.setClockTempo(bpm)
  }

  async toggleDeckSync(deck: DeckId, mode: SyncMode = "beat"): Promise<void> {
    await this.djActivationPromise
    let snapshot = this.runtime.getSnapshot()
    if (snapshot.tempoSync.decks[deck].enabled) {
      await this.runtime.toggleSync(deck, mode)
      return
    }

    await this.ensureStretchDeck(deck)
    snapshot = this.runtime.getSnapshot()
    const master = snapshot.tempoSync.master
    if (master !== "clock" && master !== deck) await this.ensureStretchDeck(master)
    await this.runtime.toggleSync(deck, mode)
  }

  beginTempoNudge(deck: DeckId, direction: TempoNudgeDirection): void {
    this.runtime.beginTempoNudge(deck, direction)
  }

  endTempoNudge(deck: DeckId): void {
    this.runtime.endTempoNudge(deck)
  }

  async toggleDeck(deck: DeckId): Promise<void> {
    const before = this.runtime.getSnapshot()
    const starting = before.decks[deck].transport !== "playing"
    if (starting && before.tempoSync.decks[deck].enabled) {
      await this.ensureStretchDeck(deck)
      const master = this.runtime.getSnapshot().tempoSync.master
      if (master !== "clock" && master !== deck) await this.ensureStretchDeck(master)
    }
    if (this.runtime.isStretchDeck(deck)) {
      const transport = this.runtime.getSnapshot().decks[deck].transport
      if (transport === "playing") await this.runtime.pauseDeck(deck)
      else await this.runtime.playDeck(deck)
      return
    }
    const element = this.elementForDeck(deck)
    if (!element) return
    if (element.paused) await element.play()
    else element.pause()
  }

  setMediaSession(track: TrackSummary, artworkUrl?: string) {
    if (!("mediaSession" in navigator)) return
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title,
      artist: track.artists.map((a) => a.name).join(", "),
      album: track.release?.title ?? "",
      // No `type` here — Chrome validates the fetched resource's actual
      // Content-Type against a declared one and silently drops the artwork
      // on mismatch, and the backend cover endpoint proxies Navidrome's
      // content-type as-is (jpeg or png depending on the source file).
      artwork: artworkUrl ? [{ src: artworkUrl, sizes: "512x512" }] : [],
    })
  }

  registerMediaSessionHandlers(handlers: {
    play?(): void
    pause?(): void
    nexttrack?(): void
    previoustrack?(): void
  }) {
    if (!("mediaSession" in navigator)) return
    for (const [action, handler] of Object.entries(handlers)) {
      try {
        navigator.mediaSession.setActionHandler(action as MediaSessionAction, handler ?? null)
      } catch {
        // browser may not support all actions
      }
    }
  }

  private queueStretchUpgrade(
    deck: DeckId,
    source: TrackSource,
    options: { readonly startAtSeconds?: number; readonly autoplay?: boolean } = {},
  ): Promise<DeckSourceUpgradeResult> {
    const pending = this.runtime.upgradeDeckSource(deck, source, options).catch((error: Error) => ({
      upgraded: false,
      kind: "media-element" as const,
      reason: error.message,
    }))
    this.upgradePromises[deck] = pending
    void pending.finally(() => {
      if (this.upgradePromises[deck] === pending) this.upgradePromises[deck] = null
    })
    return pending
  }

  private async ensureStretchDeck(deck: DeckId): Promise<DeckSourceUpgradeResult | null> {
    if (this.runtime.isStretchDeck(deck)) {
      return { upgraded: true, kind: "signalsmith", reason: null }
    }
    const queued = this.upgradePromises[deck]
    if (queued) {
      const result = await queued
      if (result.upgraded || this.runtime.isStretchDeck(deck)) return result
    }

    const candidate = this.stretchCandidateForDeck(deck)
    if (!candidate) return null
    const snapshot = this.runtime.getSnapshot().decks[deck]
    const startAtSeconds = candidate.element
      ? candidate.element.currentTime
      : snapshot.anchor?.mediaSeconds ?? 0
    const autoplay = snapshot.transport === "playing" || (candidate.element ? !candidate.element.paused : false)
    const result = await this.queueStretchUpgrade(deck, candidate.source, { startAtSeconds, autoplay })
    if (result.upgraded) candidate.element?.pause()
    return result
  }

  private stretchCandidateForDeck(deck: DeckId): {
    readonly source: TrackSource
    readonly element: HTMLAudioElement | null
  } | null {
    if (deck === this.runtime.programDeck && this.activeTrackId !== null) {
      const url = this.activeObjectUrl ?? this.activeNetworkUrl ?? this.el.currentSrc ?? this.el.src
      if (!url) return null
      return {
        source: {
          url,
          trackId: this.activeTrackId,
          queueItemId: this.activeQueueItemId,
          blob: this.activeBlob ?? undefined,
        },
        element: this.el,
      }
    }
    if (this.djDeck?.deck === deck) {
      return {
        source: {
          url: this.djDeck.objectUrl,
          trackId: this.djDeck.trackId,
          queueItemId: this.djDeck.queueItemId,
          blob: this.djDeck.blob,
        },
        element: this.djDeck.element,
      }
    }
    return null
  }

  private syncRuntimeCallbacks(): void {
    if (!this.graphActive) return
    const snapshot = this.runtime.getSnapshot()
    if (!snapshot.programDeck) return
    const deck = snapshot.decks[snapshot.programDeck]
    if (deck.sourceKind !== "signalsmith") return
    const duration = deck.duration ?? 0
    const currentTime = deck.anchor?.mediaSeconds ?? 0
    this.callbacks?.onTimeUpdate(currentTime, duration)
    if (duration > 0) this.reportFullyBuffered()
    if (deck.transport === this.lastRuntimeTransport) return
    const previous = this.lastRuntimeTransport
    this.lastRuntimeTransport = deck.transport
    const state: PlaybackState = deck.transport === "playing"
      ? "playing"
      : deck.transport === "loading"
        ? "loading"
        : deck.transport === "error"
          ? "error"
          : deck.transport === "idle"
            ? "idle"
            : "paused"
    this.callbacks?.onPlaybackStateChange(state)
    if (deck.transport === "ended" && previous !== "ended") this.callbacks?.onEnded()
  }

  private createElement(): HTMLAudioElement {
    const el = new Audio()
    el.preload = "auto"
    this.attachListeners(el)
    return el
  }

  private attachListeners(el: HTMLAudioElement) {
    el.addEventListener("timeupdate", () => {
      if (el !== this.el) return
      this.callbacks?.onTimeUpdate(el.currentTime, el.duration || 0)
    })

    const reportBuffered = () => {
      if (el !== this.el) return
      if (this.fullyBufferedReported) {
        this.callbacks?.onBufferUpdate([{ start: 0, end: 1 }])
        return
      }
      if (!Number.isFinite(el.duration) || el.duration <= 0) return
      // A media element may retain several disjoint ranges after seeking.
      // Preserve every segment so the UI never paints an unloaded gap as
      // downloaded content.
      const ranges: BufferedRange[] = []
      const { buffered } = el
      for (let i = 0; i < buffered.length; i++) {
        ranges.push({
          start: Math.max(0, Math.min(1, buffered.start(i) / el.duration)),
          end: Math.max(0, Math.min(1, buffered.end(i) / el.duration)),
        })
      }
      this.callbacks?.onBufferUpdate(ranges)
      if (this.bufferCoversDuration(el.buffered, el.duration)) this.reportFullyBuffered()
    }

    el.addEventListener("progress", reportBuffered)
    el.addEventListener("loadedmetadata", reportBuffered)
    el.addEventListener("durationchange", reportBuffered)
    el.addEventListener("canplaythrough", reportBuffered)

    el.addEventListener("play", () => {
      if (el !== this.el) return
      this.callbacks?.onPlaybackStateChange("playing")
    })

    // "playing" fires after buffering resumes — fixes spinner stuck after "waiting"
    el.addEventListener("playing", () => {
      if (el !== this.el) return
      this.callbacks?.onPlaybackStateChange("playing")
    })

    el.addEventListener("pause", () => {
      if (el !== this.el) return
      if (!el.ended) this.callbacks?.onPlaybackStateChange("paused")
    })

    el.addEventListener("waiting", () => {
      if (el !== this.el) return
      this.callbacks?.onPlaybackStateChange("loading")
    })

    el.addEventListener("canplay", () => {
      // only emit if we were loading — play/pause events handle the rest
    })

    el.addEventListener("ended", () => {
      if (el !== this.el) return
      this.callbacks?.onEnded()
    })

    el.addEventListener("error", () => {
      if (el !== this.el) return
      const err = el.error
      const msg = err ? `Media error ${err.code}: ${err.message}` : "Unknown audio error"
      this.callbacks?.onPlaybackStateChange("error")
      this.callbacks?.onError(msg)
    })
  }

  private bufferCoversDuration(buffered: TimeRanges, duration: number): boolean {
    if (!Number.isFinite(duration) || duration <= 0 || buffered.length === 0) return false
    const tolerance = 0.25
    let coveredEnd = 0
    for (let i = 0; i < buffered.length; i++) {
      const start = buffered.start(i)
      const end = buffered.end(i)
      if (start > coveredEnd + tolerance) return false
      coveredEnd = Math.max(coveredEnd, end)
      if (coveredEnd >= duration - tolerance) return true
    }
    return false
  }

  private reportFullyBuffered() {
    if (this.fullyBufferedReported || this.activeTrackId === null) return
    this.fullyBufferedReported = true
    this.cancelActiveCache()
    this.callbacks?.onBufferUpdate([{ start: 0, end: 1 }])
    // Native buffering can independently reach full coverage (via `progress`/
    // `canplaythrough`) before the explicit cacheActiveTrack() Blob swap
    // finishes. If a network seek is still pending in that case, the element
    // is now confirmed fully local — apply it directly instead of leaving
    // playback paused with no swap ever coming to resolve it.
    // Guarded on activeNetworkUrl: cacheActiveTrack()'s own success path
    // already cleared it and swapped this.el to the new Blob element via
    // activateCachedSource() *before* calling us — that element's resume()
    // (on its own loadedmetadata) is the one that must apply pendingNetworkSeek,
    // not this fallback, or the position gets applied against a duration that
    // hasn't loaded yet and the swap's own resume() finds nothing left to do.
    if (this.activeNetworkUrl && this.pendingNetworkSeek && Number.isFinite(this.el.duration) && this.el.duration > 0) {
      const pendingSeek = this.pendingNetworkSeek
      this.pendingNetworkSeek = null
      this.el.currentTime = pendingSeek.fraction * this.el.duration
      this.callbacks?.onSeekBufferingChange?.(false)
      if (pendingSeek.wasPlaying) {
        void this.el.play().catch((error: Error) => {
          this.callbacks?.onPlaybackStateChange("error")
          this.callbacks?.onError(error.message)
        })
      }
    }
    this.callbacks?.onFullyBuffered?.(this.activeTrackId, this.activeProfileKey)
  }

  private elementForDeck(deck: DeckId): HTMLAudioElement | null {
    if (deck === this.runtime.programDeck) return this.el
    if (this.djDeck?.deck === deck) return this.djDeck.element
    if (this.retired?.deck === deck) return this.retired.element
    return null
  }

  private cacheActiveTrack() {
    const trackId = this.activeTrackId
    const profileKey = this.activeProfileKey
    const url = this.activeNetworkUrl
    if (trackId === null || !url || this.fullyBufferedReported) return
    if (
      this.activeCacheTarget?.trackId === trackId
      && this.activeCacheTarget.profileKey === profileKey
      && this.activeCacheTarget.url === url
    ) return

    this.cancelActiveCache()
    const controller = new AbortController()
    const target = { trackId, profileKey, url }
    this.activeCacheController = controller
    this.activeCacheTarget = target
    playerLog("buffer", "force-cache current", { trackId, profile: profileKey })

    void this.fetchAudioBlob(url, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted || this.activeCacheTarget !== target) return
        if (
          this.activeTrackId !== trackId
          || this.activeProfileKey !== profileKey
          || this.activeNetworkUrl !== url
        ) return

        if (this.activeObjectUrl) URL.revokeObjectURL(this.activeObjectUrl)
        this.activeObjectUrl = URL.createObjectURL(blob)
        this.activeBlob = blob
        this.activeNetworkUrl = null
        this.activeCacheController = null
        this.activeCacheTarget = null
        this.activateCachedSource(this.activeObjectUrl)
        playerLog("buffer", "current Blob ready", { trackId, profile: profileKey })
        this.reportFullyBuffered()
      })
      // Native media playback remains the fallback. A later play attempt may
      // retry the explicit cache without turning the player into error.
      .catch((error: Error) => {
        if (error.name === "AbortError") return
        const stillCurrent = this.activeTrackId === trackId
          && this.activeProfileKey === profileKey
          && this.activeNetworkUrl === url
        if (stillCurrent && this.activeCacheRetryCount < 1) {
          this.activeCacheRetryCount += 1
          this.activeCacheTarget = null
          this.activeCacheController = null
          playerLog("buffer", "force-cache retry", { trackId, profile: profileKey })
          this.cacheActiveTrack()
          return
        }
        playerLog("buffer", "force-cache failed", {
          trackId,
          profile: profileKey,
          message: error.message,
        })
        if (this.pendingNetworkSeek) {
          this.pendingNetworkSeek = null
          this.callbacks?.onSeekBufferingChange?.(false)
        }
        this.callbacks?.onError(error.message)
      })
      .finally(() => {
        if (this.activeCacheController === controller) this.activeCacheController = null
        if (this.activeCacheTarget === target) this.activeCacheTarget = null
      })
  }

  private cancelActiveCache() {
    this.activeCacheController?.abort()
    this.activeCacheController = null
    this.activeCacheTarget = null
  }

  private async fetchAudioBlob(url: string, signal: AbortSignal): Promise<Blob> {
    const response = await fetch(url, {
      credentials: "same-origin",
      signal,
    })
    if (!response.ok) throw new Error(`Active audio cache failed: HTTP ${response.status}`)
    return response.blob()
  }

  private activateCachedSource(objectUrl: string) {
    const prev = this.el
    const position = prev.currentTime
    // Our own seek() pauses the raw element while buffering, so `!prev.paused`
    // would always read false here; the seek's own play/pause intent
    // (pendingNetworkSeek.wasPlaying) is the source of truth when a seek is
    // in flight.
    const shouldResume = this.pendingNetworkSeek ? this.pendingNetworkSeek.wasPlaying : !prev.paused
    const next = this.createElement()
    next.volume = prev.volume
    next.muted = prev.muted
    next.src = objectUrl

    // Make stale element events inert before unloading it. The replacement is
    // local, so metadata/seek do not require another network request.
    this.el = next
    if (this.graphActive) {
      this.runtime.routeProgramElement(next, this.activeTrackId, this.activeQueueItemId)
    }
    prev.pause()
    prev.src = ""
    prev.load()

    const resume = () => {
      next.removeEventListener("loadedmetadata", resume)
      if (next !== this.el) return
      const pendingSeek = this.pendingNetworkSeek
      const requestedPosition = pendingSeek === null
        ? position
        : pendingSeek.fraction * next.duration
      this.pendingNetworkSeek = null
      if (pendingSeek !== null) this.callbacks?.onSeekBufferingChange?.(false)
      next.currentTime = Math.min(requestedPosition, next.duration || requestedPosition)
      if (shouldResume) {
        void next.play().catch((error: Error) => {
          this.callbacks?.onPlaybackStateChange("error")
          this.callbacks?.onError(error.message)
        })
      }
    }
    next.addEventListener("loadedmetadata", resume)
    next.load()
  }
}

// Singleton — created once at module load, lives outside React tree
export const playerPlayback = new PlayerPlaybackFacade()
