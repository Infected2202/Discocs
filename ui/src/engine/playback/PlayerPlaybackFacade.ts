import type { TrackSummary } from "@/api/types"
import { playerLog } from "@/lib/playerLogger"
import { PlaybackEngine } from "./PlaybackEngine"
import type {
  DeckId,
  DeckSourceUpgradeResult,
  EqBand,
  HandoverResult,
  PlaybackEngineSnapshot,
  TrackSource,
  TransportState,
} from "./types"

export type PlaybackState = "idle" | "loading" | "playing" | "paused" | "error"
export interface BufferedRange {
  start: number
  end: number
}

interface AudioEngineCallbacks {
  onTimeUpdate(currentTime: number, duration: number): void
  onPlaybackStateChange(state: PlaybackState): void
  onBufferUpdate(ranges: BufferedRange[]): void
  onFullyBuffered?(trackId: number, profileKey: string): void
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
  private activeObjectUrl: string | null = null
  private activeCacheController: AbortController | null = null
  private activeCacheTarget: { trackId: number; profileKey: string; url: string } | null = null
  private fullyBufferedReported = false
  private prefetched: {
    trackId: number
    queueItemId: string | null
    profileKey: string
    objectUrl: string
    blob: Blob
  } | null = null
  private prepared: {
    trackId: number
    queueItemId: string | null
    profileKey: string
    objectUrl: string
    blob: Blob
    element: HTMLAudioElement
    deck: DeckId
  } | null = null
  private retired: { element: HTMLAudioElement; objectUrl: string | null; blob: Blob | null; deck: DeckId } | null = null
  private prefetchController: AbortController | null = null
  private prefetchTarget: { trackId: number; profileKey: string } | null = null
  // False = ordinary direct-<audio> playback (no AudioContext, reliable mobile
  // background). True = DJ mode: audio routed through the Web Audio mixer graph.
  // The graph is only ever created once DJ mode is activated by a user gesture.
  private graphActive = false
  private activeBlob: Blob | null = null
  private readonly upgradePromises: Record<DeckId, Promise<DeckSourceUpgradeResult> | null> = { A: null, B: null }
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
    this.runtime.routeProgramElement(this.el, trackId, queueItemId)
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
    if (
      this.prefetched?.trackId === trackId
      && this.prefetched.profileKey === profileKey
      && this.prefetched.queueItemId === queueItemId
    ) return
    if (this.prefetchTarget?.trackId === trackId && this.prefetchTarget.profileKey === profileKey) return
    this.cancelPrefetch()
    this.clearPrefetched()
    const controller = new AbortController()
    this.prefetchController = controller
    this.prefetchTarget = { trackId, profileKey }
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
      this.prefetched = { trackId, queueItemId, profileKey, objectUrl, blob }
      const element = this.createElement()
      element.volume = this.el.volume
      element.muted = this.el.muted
      element.src = objectUrl
      element.load()
      const deck = this.runtime.routeIncomingElement(element, trackId, queueItemId)
      if (deck) {
        this.prepared = { trackId, queueItemId, profileKey, objectUrl, blob, element, deck }
        if (this.graphActive) {
          await this.queueStretchUpgrade(deck, { url: objectUrl, trackId, queueItemId, blob })
        }
      } else {
        element.src = ""
        element.load()
      }
    } finally {
      if (this.prefetchController === controller) {
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
    if (this.prepared?.objectUrl === objectUrl) {
      this.runtime.cancelIncoming()
      this.prepared.element.pause()
      this.prepared.element.src = ""
      this.prepared.element.load()
      this.prepared = null
    }
    if (this.activeObjectUrl && this.activeObjectUrl !== objectUrl) {
      URL.revokeObjectURL(this.activeObjectUrl)
    }
    this.activeObjectUrl = objectUrl
    return objectUrl
  }

  cancelPrefetch() {
    this.prefetchController?.abort()
    this.prefetchController = null
    this.prefetchTarget = null
  }

  clearPrefetched() {
    if (this.prepared) {
      this.runtime.cancelIncoming()
      this.prepared.element.pause()
      this.prepared.element.src = ""
      this.prepared.element.load()
      this.prepared = null
    }
    if (this.prefetched) URL.revokeObjectURL(this.prefetched.objectUrl)
    this.prefetched = null
  }

  hasPrepared(trackId: number, queueItemId: string): boolean {
    return this.prepared?.trackId === trackId
      && this.prepared.queueItemId === queueItemId
  }

  async handoverPrepared(clientHandoverId: string): Promise<{
    trackId: number
    queueItemId: string
    profileKey: string
    outgoingDeck: DeckId
    programDeck: DeckId
  }> {
    const incoming = this.prepared
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
    this.prepared = null
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

  async activateDjMode(): Promise<void> {
    if (this.graphActive) return
    await this.runtime.ensureReady()
    this.graphActive = true
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

    const incoming = this.prepared
    if (incoming) {
      await this.queueStretchUpgrade(incoming.deck, {
        url: incoming.objectUrl,
        trackId: incoming.trackId,
        queueItemId: incoming.queueItemId,
        blob: incoming.blob,
      })
    }
  }

  async play(): Promise<void> {
    await this.runtime.ensureReady()
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
    this.cancelActiveCache()
    if (this.activeObjectUrl) URL.revokeObjectURL(this.activeObjectUrl)
    this.activeObjectUrl = null
    this.activeBlob = null
    this.activeNetworkUrl = null
    this.activeTrackId = null
    this.activeQueueItemId = null
    this.fullyBufferedReported = false
    this.graphActive = false
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
      void this.runtime.seekDeck(deck, clamped * snapshot.duration)
        .catch((error: Error) => this.callbacks?.onError(error.message))
      return
    }
    const el = this.el
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
    if (this.prepared) this.prepared.element.volume = volume
    if (this.graphActive) this.runtime.setMasterGain(this.el.muted ? 0 : volume)
  }

  setMuted(muted: boolean) {
    this.el.muted = muted
    if (this.prepared) this.prepared.element.muted = muted
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

  async toggleDeck(deck: DeckId): Promise<void> {
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
    this.callbacks?.onFullyBuffered?.(this.activeTrackId, this.activeProfileKey)
  }

  private elementForDeck(deck: DeckId): HTMLAudioElement | null {
    if (deck === this.runtime.programDeck) return this.el
    if (this.prepared?.deck === deck) return this.prepared.element
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
        if (error.name !== "AbortError") {
          playerLog("buffer", "force-cache failed", {
            trackId,
            profile: profileKey,
            message: error.message,
          })
        }
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
    const shouldResume = !prev.paused
    const next = this.createElement()
    next.volume = prev.volume
    next.muted = prev.muted
    next.src = objectUrl

    // Make stale element events inert before unloading it. The replacement is
    // local, so metadata/seek do not require another network request.
    this.el = next
    this.runtime.routeProgramElement(next, this.activeTrackId, this.activeQueueItemId)
    prev.pause()
    prev.src = ""
    prev.load()

    const resume = () => {
      next.removeEventListener("loadedmetadata", resume)
      if (next !== this.el) return
      next.currentTime = Math.min(position, next.duration || position)
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
