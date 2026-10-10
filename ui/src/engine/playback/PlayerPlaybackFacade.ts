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
  /**
   * The current track's own buffering has settled (fully buffered, or the
   * browser idled its download with enough audio ahead, or a safety net) —
   * from here on fetching the next track no longer competes with it.
   */
  onBufferingSettled?(trackId: number, profileKey: string): void
  onSeekBufferingChange?(active: boolean): void
  onNextTrackBufferingChange?(info: NextTrackBufferInfo | null): void
  onEnded(): void
  onError(message: string): void
}

// HTMLMediaElement constants as literals: test fakes don't carry the statics.
const HAVE_NOTHING = 0
const HAVE_METADATA = 1
const HAVE_FUTURE_DATA = 3
const NETWORK_IDLE = 1
const RANGE_TOLERANCE_SECONDS = 0.25
/** Audio the browser must hold ahead of the playhead when it idles its download for that to count as settled. */
export const SETTLED_AHEAD_SECONDS = 60
/** Safety net: a buffered end at this share of the track counts as settled even if the browser keeps downloading. */
export const SETTLED_BUFFERED_FRACTION = 0.8
/** Safety net: this close to the end the next track is fetched no matter how the current one buffers. */
export const SETTLED_REMAINING_SECONDS = 45
/** A source with no data at all this long after it was opened is a lost request, not a slow one. */
export const SOURCE_STALL_MS = 10_000

/**
 * The `/tracks/{id}/audio` URL of a transcoded stream that starts `seconds`
 * into the track (server-side seek, Navidrome `timeOffset`).
 */
export function withStartOffset(url: string, seconds: number): string {
  return `${url}${url.includes("?") ? "&" : "?"}t=${seconds}`
}

function rangeContaining(ranges: TimeRanges | undefined, seconds: number): { start: number; end: number } | null {
  if (!ranges) return null
  for (let i = 0; i < ranges.length; i++) {
    const start = ranges.start(i)
    const end = ranges.end(i)
    if (start - RANGE_TOLERANCE_SECONDS <= seconds && end + RANGE_TOLERANCE_SECONDS >= seconds) return { start, end }
  }
  return null
}

function aheadKey(trackId: number, profileKey: string): string {
  return `${trackId}:${profileKey}`
}

function metadataReady(el: HTMLAudioElement): boolean {
  return el.readyState >= HAVE_METADATA || (Number.isFinite(el.duration) && el.duration > 0)
}

export class PlayerPlaybackFacade {
  private el: HTMLAudioElement
  private readonly runtime: PlaybackEngine
  private callbacks: AudioEngineCallbacks | null = null
  private activeTrackId: number | null = null
  private activeQueueItemId: string | null = null
  private activeProfileKey = "raw"
  /** Base network URL of the current track (never carries `t`); null for a local Blob. */
  private activeNetworkUrl: string | null = null
  // The track's real duration from API metadata, not the <audio> element's
  // own `.duration`. An offset stream only knows its remainder, and a
  // chunked/estimated transcode reports an unresolved or approximate
  // el.duration — the track metadata is the stable timeline for them.
  private activeKnownDuration: number | null = null
  /**
   * Seconds into the track at which the element's current source starts:
   * >0 only for a transcoded stream reloaded with `t` (server-side seek).
   * Reported position = streamOffset + el.currentTime.
   */
  private streamOffset = 0
  /** A transcoded seek reload is waiting for its first data. */
  private pendingReload: { wasPlaying: boolean; indicator: boolean } | null = null
  /** The server refused/failed an offset stream once — this track seeks natively from now on. */
  private offsetFallbackUsed = false
  private pendingMetadataAction: { el: HTMLAudioElement; handler: () => void } | null = null
  /** Bumped on every source (re)assignment, so a superseded play() can tell it was interrupted by us. */
  private sourceGeneration = 0
  /** Wall-clock time the current source was (re)assigned — for telling a lost request from a slow one. */
  private sourceOpenedAt = 0
  private playRequested = false
  private activeObjectUrl: string | null = null
  /** The whole track is local (Blob, decoded stretch deck): buffer bar is full. */
  private fullyLocal = false
  private bufferSettledReported = false
  private prefetched: {
    trackId: number
    queueItemId: string | null
    profileKey: string
    objectUrl: string
    blob: Blob
  } | null = null
  private prefetchRetryCount = 0
  /**
   * Tracks further ahead than the next one (the user's `prefetch_tracks`
   * setting above 1), fetched only once the next one is ready, keyed by
   * aheadKey(). One becoming the next track is promoted into `prefetched`
   * without a refetch; a skip straight to one consumes it directly.
   */
  private readonly aheadPool = new Map<string, { trackId: number; profileKey: string; objectUrl: string; blob: Blob }>()
  private aheadRun: { key: string; controller: AbortController; promise: Promise<void> } | null = null
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
    knownDurationSeconds?: number | null,
    startPositionSeconds: number | null = null,
  ) {
    this.cancelPendingMetadataAction()
    this.endPendingReload()
    this.playRequested = false
    const retainedBlob = fullyAvailable && this.activeObjectUrl === url ? this.activeBlob : null
    if (this.graphActive) {
      // DJ: элемент, заведённый в граф, из него не выводится
      // (createMediaElementSource необратим) — каждому треку свой элемент.
      const prev = this.el
      prev.pause()
      prev.src = ""
      prev.load()
      this.el = this.createElement()
      this.runtime.routeProgramElement(this.el, trackId, queueItemId)
      this.el.volume = prev.volume
      this.el.muted = prev.muted
    }
    // Обычный режим держит ОДИН <audio> на всё воспроизведение и только меняет
    // ему источник (openSource ниже), как обычные веб-плееры. Новый элемент на
    // каждый трек (пауза + src='' старого) рвал медиасессию Chrome на Android:
    // на смене трека пропадало медиа-уведомление, вместе с ним фоновая служба,
    // и система отрезала вкладке сеть — в фоне доигрывали только уже скачанные
    // треки. Старый ресурс освобождается самим алгоритмом загрузки элемента.
    // Без паузы: смена src останавливает старый трек сама, а лишнее событие
    // pause мигнуло бы медиасессии «на паузе». Элемент обычного режима никогда
    // не заведён в граф: DJ-режим при выходе создаёт свежий (deactivateDjMode).
    if (this.activeObjectUrl && this.activeObjectUrl !== url) {
      URL.revokeObjectURL(this.activeObjectUrl)
      this.activeObjectUrl = null
    }
    this.activeBlob = retainedBlob
    this.activeTrackId = trackId
    this.activeQueueItemId = queueItemId
    this.activeProfileKey = profileKey
    this.activeNetworkUrl = fullyAvailable || url.startsWith("blob:") ? null : url
    this.activeKnownDuration = Number.isFinite(knownDurationSeconds) && (knownDurationSeconds as number) > 0
      ? (knownDurationSeconds as number)
      : null
    this.offsetFallbackUsed = false
    this.fullyLocal = false
    this.bufferSettledReported = false
    this.lastRuntimeTransport = null
    const startSeconds = Number.isFinite(startPositionSeconds) && (startPositionSeconds as number) > 0
      ? (startPositionSeconds as number)
      : 0
    // The current track is a plain progressive stream: no full download of
    // it is ever forced. A start position is applied the same way a seek is
    // (Range for raw/Blob, `t` for a transcode).
    this.openSource(this.el, url, startSeconds, false)

    // Reset immediately — otherwise the buffered indicator briefly shows
    // the previous track's ranges. A prepared Blob is already fully local.
    this.callbacks?.onBufferUpdate(fullyAvailable ? [{ start: 0, end: 1 }] : [])
    if (fullyAvailable && trackId !== null) this.markFullyLocal()
    if (this.graphActive && trackId !== null) {
      this.queueStretchUpgrade(this.runtime.programDeck, {
        url,
        trackId,
        queueItemId,
        blob: this.activeBlob ?? undefined,
      }, startSeconds > 0 ? { startAtSeconds: startSeconds } : {})
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
    const pooled = this.takeAhead(trackId, profileKey)
    if (pooled) {
      this.prefetched = { trackId, queueItemId, profileKey, objectUrl: pooled.objectUrl, blob: pooled.blob }
      this.callbacks?.onNextTrackBufferingChange?.({ trackId, queueItemId, ready: true })
      return
    }
    this.prefetchTarget = { trackId, profileKey, queueItemId }
    this.prefetchRetryCount = 0
    this.callbacks?.onNextTrackBufferingChange?.({ trackId, queueItemId, ready: false })

    try {
      for (;;) {
        const controller = new AbortController()
        this.prefetchController = controller
        try {
          // Low priority: the current track may still be streaming (a safety
          // net can settle it before the browser is done) — it must win.
          const init: RequestInit & { priority?: "high" | "low" | "auto" } = {
            credentials: "same-origin",
            signal: controller.signal,
            priority: "low",
          }
          const response = await fetch(url, init)
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
    const next = this.prefetched?.trackId === trackId && this.prefetched.profileKey === profileKey
      ? this.prefetched
      : null
    if (next) {
      this.prefetched = null
      this.callbacks?.onNextTrackBufferingChange?.(null)
    }
    const source = next ?? this.takeAhead(trackId, profileKey)
    if (!source) return null
    const { objectUrl } = source
    this.activeBlob = source.blob
    if (this.activeObjectUrl && this.activeObjectUrl !== objectUrl) {
      URL.revokeObjectURL(this.activeObjectUrl)
    }
    this.activeObjectUrl = objectUrl
    return objectUrl
  }

  cancelPrefetch() {
    const hadTarget = this.prefetchController !== null || this.prefetchTarget !== null
    // Downloads further ahead stop too (the pool itself is kept): they must not
    // compete with a track that is starting; prefetchAhead resumes them.
    this.aheadRun?.controller.abort()
    this.aheadRun = null
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
   * Keep exactly `targets` — the tracks after the next one, in queue order —
   * downloaded ahead: drop pooled tracks no longer wanted, then fetch the
   * missing ones one by one at low priority. Runs only once the next track
   * is ready (it never competes with it); a call with other targets
   * supersedes a running one, the same targets join it.
   */
  prefetchAhead(targets: ReadonlyArray<{ trackId: number; url: string; profileKey: string }>): Promise<void> {
    const wanted = new Set(targets.map((target) => aheadKey(target.trackId, target.profileKey)))
    for (const [key, entry] of this.aheadPool) {
      if (wanted.has(key)) continue
      URL.revokeObjectURL(entry.objectUrl)
      this.aheadPool.delete(key)
    }
    const runKey = [...wanted].join(",")
    if (this.aheadRun?.key === runKey) return this.aheadRun.promise
    this.aheadRun?.controller.abort()
    this.aheadRun = null
    if (this.prefetchTarget !== null) return Promise.resolve()
    const missing = targets.filter((target) => (
      !this.aheadPool.has(aheadKey(target.trackId, target.profileKey))
      && !(this.prefetched?.trackId === target.trackId && this.prefetched.profileKey === target.profileKey)
    ))
    if (missing.length === 0) return Promise.resolve()
    const controller = new AbortController()
    const promise = this.fetchAhead(missing, controller).finally(() => {
      if (this.aheadRun?.controller === controller) this.aheadRun = null
    })
    this.aheadRun = { key: runKey, controller, promise }
    return promise
  }

  private async fetchAhead(
    targets: ReadonlyArray<{ trackId: number; url: string; profileKey: string }>,
    controller: AbortController,
  ): Promise<void> {
    try {
      for (const { trackId, url, profileKey } of targets) {
        const init: RequestInit & { priority?: "high" | "low" | "auto" } = {
          credentials: "same-origin",
          signal: controller.signal,
          priority: "low",
        }
        const response = await fetch(url, init)
        if (!response.ok) throw new Error(`Audio prefetch failed: HTTP ${response.status}`)
        const blob = await response.blob()
        if (controller.signal.aborted) return
        this.aheadPool.set(aheadKey(trackId, profileKey), {
          trackId,
          profileKey,
          objectUrl: URL.createObjectURL(blob),
          blob,
        })
        playerLog("buffer", "prefetched ahead", { trackId, profile: profileKey })
      }
    } catch (error) {
      if (controller.signal.aborted || (error as Error).name === "AbortError") return
      throw error
    }
  }

  /** Remove a pooled track from the ahead pool and hand over its Blob, if it is there. */
  private takeAhead(trackId: number, profileKey: string): { objectUrl: string; blob: Blob } | null {
    const key = aheadKey(trackId, profileKey)
    const entry = this.aheadPool.get(key)
    if (!entry) return null
    this.aheadPool.delete(key)
    return entry
  }

  private clearAheadPool(): void {
    this.aheadRun?.controller.abort()
    this.aheadRun = null
    for (const entry of this.aheadPool.values()) URL.revokeObjectURL(entry.objectUrl)
    this.aheadPool.clear()
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
    this.cancelPendingMetadataAction()
    this.endPendingReload()
    this.el = incoming.element
    this.retired = { element: previous, objectUrl: previousObjectUrl, blob: previousBlob, deck: result.outgoingDeck }
    this.activeTrackId = incoming.trackId
    this.activeQueueItemId = incoming.queueItemId
    this.activeProfileKey = incoming.profileKey
    this.activeNetworkUrl = null
    this.activeKnownDuration = null
    this.streamOffset = 0
    this.offsetFallbackUsed = false
    this.activeObjectUrl = incoming.objectUrl
    this.activeBlob = incoming.blob
    // The store records the settled source for a handover itself.
    this.fullyLocal = true
    this.bufferSettledReported = true
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
      // The base URL, never the `t` offset stream: the stretch deck fetches
      // and decodes the whole track and starts at the absolute position.
      const sourceUrl = this.activeObjectUrl ?? this.activeNetworkUrl ?? element.currentSrc ?? element.src
      const result = await this.queueStretchUpgrade(this.runtime.programDeck, {
        url: sourceUrl,
        trackId,
        queueItemId: this.activeQueueItemId,
        blob: this.activeBlob ?? undefined,
      }, {
        startAtSeconds: this.streamOffset + element.currentTime,
        autoplay: !element.paused,
      })
      if (result.upgraded && element === this.el) {
        element.pause()
        this.endPendingReload()
        this.markFullyLocal()
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
    // источник из самого элемента. Сетевой трек — всегда базовый URL (без `t`):
    // позицию openSource применит сам (Range или `t` для транскода).
    const sourceUrl = this.activeObjectUrl ?? this.activeNetworkUrl ?? previous.currentSrc ?? previous.src

    // Свежий, НЕ заведённый в граф <audio> на той же позиции. При клике разрыв
    // допустим: createMediaElementSource необратим, поэтому нужен новый элемент.
    const next = this.createElement()
    next.volume = previous.volume
    next.muted = previous.muted
    this.cancelPendingMetadataAction()
    this.endPendingReload()
    this.el = next
    this.graphActive = false
    this.djActivationPromise = null
    this.lastRuntimeTransport = null
    this.upgradePromises.A = null
    this.upgradePromises.B = null

    // A stretch upgrade marked the track fully local; back on the network
    // stream the buffer bar must show real ranges again.
    this.fullyLocal = Boolean(sourceUrl) && sourceUrl !== this.activeNetworkUrl
    if (sourceUrl) this.openSource(next, sourceUrl, position, wasPlaying)

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
    this.playRequested = true
    if (this.pendingReload) this.pendingReload.wasPlaying = true
    // Обычный режим не трогает AudioContext — иначе создание/резюм контекста
    // снова привязывает воспроизведение к суспендируемому в фоне графу.
    if (this.graphActive) await this.runtime.ensureReady()
    const deck = this.runtime.programDeck
    await this.upgradePromises[deck]
    if (this.runtime.isStretchDeck(deck)) {
      await this.runtime.playDeck(deck)
      this.markFullyLocal()
      return
    }
    const el = this.el
    const generation = this.sourceGeneration
    try {
      await el.play()
    } catch (error) {
      // A seek reload (or the offset-stream fallback) replaced this element's
      // source while play() was pending — that rejects the pending promise
      // (AbortError / NotSupportedError), but the reload owns resuming now.
      if (el === this.el && generation !== this.sourceGeneration) return
      throw error
    }
  }

  pause() {
    this.playRequested = false
    if (this.pendingReload) this.pendingReload.wasPlaying = false
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
    this.clearAheadPool()
    this.clearDjDeck()
    this.cancelPendingMetadataAction()
    this.endPendingReload()
    if (this.activeObjectUrl) URL.revokeObjectURL(this.activeObjectUrl)
    this.activeObjectUrl = null
    this.activeBlob = null
    this.activeKnownDuration = null
    this.activeNetworkUrl = null
    this.streamOffset = 0
    this.offsetFallbackUsed = false
    this.playRequested = false
    this.activeTrackId = null
    this.activeQueueItemId = null
    this.fullyLocal = false
    this.bufferSettledReported = false
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
      void this.runtime.seekDeck(deck, clamped * snapshot.duration)
        .catch((error: Error) => this.callbacks?.onError(error.message))
      return
    }
    const duration = this.trackDuration()
    if (duration !== null) {
      this.seekToTrackSeconds(clamped * duration, true)
      return
    }
    // No duration anywhere yet (fresh element, no API metadata): resolve the
    // fraction once the source's metadata arrives instead of dropping it.
    this.whenMetadataReady(this.el, () => {
      const resolved = this.trackDuration()
      if (resolved !== null) this.seekToTrackSeconds(clamped * resolved, true)
    })
  }

  seekToSeconds(seconds: number) {
    this.seekToTrackSeconds(seconds, true)
  }

  seekDeckToSeconds(deck: DeckId, seconds: number): void {
    if (this.runtime.isStretchDeck(deck)) {
      void this.runtime.seekDeck(deck, seconds).catch((error: Error) => this.callbacks?.onError(error.message))
      return
    }
    const element = this.elementForDeck(deck)
    if (!element || !Number.isFinite(seconds)) return
    if (element === this.el) {
      this.seekToTrackSeconds(seconds, true)
      return
    }
    const maximum = Number.isFinite(element.duration) && element.duration > 0
      ? element.duration
      : Number.POSITIVE_INFINITY
    element.currentTime = Math.min(Math.max(0, seconds), maximum)
  }

  /**
   * Position the current track without the seek-buffering indicator (e.g.
   * right after load()). Same mechanism as a seek: deferred to metadata for
   * Range/Blob sources, a `t` reload for a transcode.
   */
  resumeAtSeconds(seconds: number) {
    this.seekToTrackSeconds(seconds, false)
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
      : this.streamOffset + this.el.currentTime
  }

  get duration() {
    const deck = this.runtime.programDeck
    const snapshot = this.runtime.getSnapshot().decks[deck]
    return snapshot.sourceKind === "signalsmith"
      ? snapshot.duration ?? 0
      : this.trackDuration() ?? this.el.duration
  }

  /**
   * The current track's source is dead and play() on the same element cannot
   * revive it: a media error, or still no data long after it was opened — a
   * request lost while the network was gone (a background transition on a
   * phone whose network is asleep). Only reloading the track helps. The DJ
   * graph manages its own decks and is never reported here.
   */
  get sourceFailed(): boolean {
    if (this.graphActive) return false
    const el = this.el
    if (!el.src) return false
    if (el.error) return true
    return el.readyState === HAVE_NOTHING && Date.now() - this.sourceOpenedAt >= SOURCE_STALL_MS
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
    if (!element || !Number.isFinite(element.currentTime)) return null
    return element === this.el ? this.streamOffset + element.currentTime : element.currentTime
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
      ? candidate.element.currentTime + (candidate.element === this.el ? this.streamOffset : 0)
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
    if (duration > 0) this.markFullyLocal()
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
      this.callbacks?.onTimeUpdate(this.streamOffset + el.currentTime, this.trackDuration() ?? 0)
      this.checkBufferingSettled(el, "timeupdate")
    })

    const reportBuffered = () => {
      if (el !== this.el) return
      if (this.fullyLocal) {
        this.callbacks?.onBufferUpdate([{ start: 0, end: 1 }])
        return
      }
      const duration = this.trackDuration()
      if (duration === null) return
      // A media element may retain several disjoint ranges after seeking.
      // Preserve every segment so the UI never paints an unloaded gap as
      // downloaded content. Ranges of an offset (`t`) stream are shifted
      // onto the track's own timeline.
      const ranges: BufferedRange[] = []
      const { buffered } = el
      for (let i = 0; i < buffered.length; i++) {
        ranges.push({
          start: Math.max(0, Math.min(1, (this.streamOffset + buffered.start(i)) / duration)),
          end: Math.max(0, Math.min(1, (this.streamOffset + buffered.end(i)) / duration)),
        })
      }
      this.callbacks?.onBufferUpdate(ranges)
      this.checkBufferingSettled(el, "progress")
    }

    el.addEventListener("progress", reportBuffered)
    el.addEventListener("loadedmetadata", reportBuffered)
    el.addEventListener("durationchange", reportBuffered)
    el.addEventListener("canplaythrough", reportBuffered)
    // The browser idled its download (mobile browsers stop around 80-90%).
    el.addEventListener("suspend", () => this.checkBufferingSettled(el, "suspend"))

    el.addEventListener("play", () => {
      if (el !== this.el) return
      this.callbacks?.onPlaybackStateChange("playing")
    })

    // "playing" fires after buffering resumes — fixes spinner stuck after "waiting"
    el.addEventListener("playing", () => {
      if (el !== this.el) return
      this.endPendingReload()
      this.callbacks?.onPlaybackStateChange("playing")
    })

    el.addEventListener("pause", () => {
      if (el !== this.el) return
      // A seek reload of a playing stream interrupts it; that is not a user
      // pause (a real one clears pendingReload.wasPlaying first).
      if (this.pendingReload?.wasPlaying) return
      // The element is reused across tracks: a pause queued by the previous
      // source and dispatched after the next one already started is stale.
      if (!el.paused) return
      if (!el.ended) this.callbacks?.onPlaybackStateChange("paused")
    })

    el.addEventListener("waiting", () => {
      if (el !== this.el) return
      this.callbacks?.onPlaybackStateChange("loading")
    })

    el.addEventListener("canplay", () => {
      if (el !== this.el) return
      // A paused seek reload is done once the new offset has data.
      if (this.pendingReload && !this.pendingReload.wasPlaying) this.endPendingReload()
    })

    el.addEventListener("ended", () => {
      if (el !== this.el) return
      this.callbacks?.onEnded()
    })

    el.addEventListener("error", () => {
      if (el !== this.el) return
      if (this.fallBackFromOffsetStream(el)) return
      this.endPendingReload()
      const err = el.error
      const msg = err ? `Media error ${err.code}: ${err.message}` : "Unknown audio error"
      this.callbacks?.onPlaybackStateChange("error")
      this.callbacks?.onError(msg)
    })
  }

  /** Base network source of a transcode profile — no Range support, seeks server-side with `t`. */
  private isTranscodedNetworkSource(): boolean {
    return this.activeNetworkUrl !== null && this.activeProfileKey !== "raw"
  }

  /**
   * The track's duration on its own timeline. Raw/Blob sources trust the
   * element (exact). A transcode trusts the API metadata: its el.duration is
   * a Content-Length estimate, unresolved when chunked, and only the
   * remainder for an offset stream.
   */
  private trackDuration(): number | null {
    const el = this.el
    const elementDuration = Number.isFinite(el.duration) && el.duration > 0 ? el.duration : null
    if (!this.isTranscodedNetworkSource() && this.streamOffset === 0 && elementDuration !== null) {
      return elementDuration
    }
    if (this.activeKnownDuration !== null) return this.activeKnownDuration
    return elementDuration !== null ? this.streamOffset + elementDuration : null
  }

  /** Whole-second server offset for a start/seek target, 0 when the source seeks natively. */
  private serverOffsetFor(sourceUrl: string, targetSeconds: number): number {
    if (
      sourceUrl !== this.activeNetworkUrl
      || !this.isTranscodedNetworkSource()
      || this.offsetFallbackUsed
      || targetSeconds < 1
    ) return 0
    let offset = Math.floor(targetSeconds)
    if (this.activeKnownDuration !== null) {
      offset = Math.min(offset, Math.max(0, Math.ceil(this.activeKnownDuration) - 1))
    }
    return offset
  }

  /**
   * Point `el` at `sourceUrl` starting `startSeconds` into the track. A
   * transcode starts server-side (`t`, the element's timeline then begins at
   * streamOffset); everything else — raw Range-capable streams and Blobs —
   * seeks natively once metadata is known.
   */
  private openSource(el: HTMLAudioElement, sourceUrl: string, startSeconds: number, autoplay: boolean): void {
    this.cancelPendingMetadataAction()
    const offset = this.serverOffsetFor(sourceUrl, startSeconds)
    this.streamOffset = offset
    this.sourceGeneration += 1
    this.sourceOpenedAt = Date.now()
    el.src =offset > 0 ? withStartOffset(sourceUrl, offset) : sourceUrl
    el.load()
    const nativeStart = offset > 0 ? 0 : startSeconds
    if (nativeStart > 0) {
      // Position before resuming, so nothing from 0 is audible first.
      this.whenMetadataReady(el, () => {
        const limit = Number.isFinite(el.duration) && el.duration > 0 ? el.duration : nativeStart
        el.currentTime = Math.min(nativeStart, limit)
        if (autoplay) this.resumeElement(el)
      })
    } else if (autoplay) {
      this.resumeElement(el)
    }
  }

  private resumeElement(el: HTMLAudioElement): void {
    const generation = this.sourceGeneration
    void el.play().catch((error: Error) => {
      if (el !== this.el || generation !== this.sourceGeneration || error.name === "AbortError") return
      this.callbacks?.onPlaybackStateChange("error")
      this.callbacks?.onError(error.message)
    })
  }

  private seekToTrackSeconds(seconds: number, showIndicator: boolean): void {
    if (!Number.isFinite(seconds)) return
    const deck = this.runtime.programDeck
    if (this.runtime.isStretchDeck(deck)) {
      void this.runtime.seekDeck(deck, seconds).catch((error: Error) => this.callbacks?.onError(error.message))
      return
    }
    const duration = this.trackDuration()
    const target = Math.max(0, duration !== null ? Math.min(seconds, duration) : seconds)
    const el = this.el
    if (!this.isTranscodedNetworkSource() || this.offsetFallbackUsed) {
      // Raw (Range-capable) stream or local Blob: the browser fetches the
      // target range itself — no pause, no waiting for the whole file. Only a
      // not-yet-known duration defers the write (it would be dropped).
      this.whenMetadataReady(el, () => {
        if (el !== this.el) return
        el.currentTime = Math.max(0, target - this.streamOffset)
      })
      playerLog("seek", "native", { trackId: this.activeTrackId, targetSeconds: Math.round(target * 100) / 100 })
      return
    }
    const local = target - this.streamOffset
    if (local >= 0 && this.canSeekWithinStream(el, local)) {
      // Already buffered and seekable in the current (possibly offset)
      // stream: a pointer move over bytes the browser holds.
      this.cancelPendingMetadataAction()
      el.currentTime = local
      playerLog("seek", "within transcoded stream", { trackId: this.activeTrackId, targetSeconds: Math.round(target * 100) / 100 })
      return
    }
    this.reloadAtOffset(target, showIndicator)
  }

  /**
   * A transcode ignores Range (Navidrome answers `Accept-Ranges: none`), and
   * writing currentTime outside `seekable` snaps a browser back to 0 — so a
   * native seek is only safe where the target is both buffered and seekable.
   */
  private canSeekWithinStream(el: HTMLAudioElement, localSeconds: number): boolean {
    return rangeContaining(el.buffered, localSeconds) !== null
      && rangeContaining(el.seekable as TimeRanges | undefined, localSeconds) !== null
  }

  /** Server-side seek: reload the transcode starting at `targetSeconds` (`t`). */
  private reloadAtOffset(targetSeconds: number, showIndicator: boolean): void {
    const url = this.activeNetworkUrl
    if (url === null) return
    const el = this.el
    const wasPlaying = this.pendingReload?.wasPlaying ?? !el.paused
    const indicator = showIndicator || (this.pendingReload?.indicator ?? false)
    if (indicator && !this.pendingReload?.indicator) this.callbacks?.onSeekBufferingChange?.(true)
    this.pendingReload = { wasPlaying, indicator }
    this.openSource(el, url, targetSeconds, wasPlaying)
    this.callbacks?.onBufferUpdate([])
    playerLog("seek", "transcoded reload", {
      trackId: this.activeTrackId,
      targetSeconds: Math.round(targetSeconds * 100) / 100,
      offset: this.streamOffset,
    })
  }

  private endPendingReload(): void {
    const pending = this.pendingReload
    if (!pending) return
    this.pendingReload = null
    if (pending.indicator) this.callbacks?.onSeekBufferingChange?.(false)
  }

  /**
   * An offset stream failed (e.g. the server refused `t` because this track
   * is served from a local file, or a backend without `t` support): reopen
   * the plain source once and seek natively instead of surfacing an error.
   */
  private fallBackFromOffsetStream(el: HTMLAudioElement): boolean {
    const url = this.activeNetworkUrl
    if (this.streamOffset <= 0 || url === null || this.offsetFallbackUsed) return false
    const target = this.streamOffset
    const autoplay = this.pendingReload?.wasPlaying ?? this.playRequested
    if (this.pendingReload) this.pendingReload.wasPlaying = autoplay
    this.offsetFallbackUsed = true
    playerLog("seek", "offset stream failed, falling back to native seek", {
      trackId: this.activeTrackId,
      targetSeconds: target,
    })
    this.openSource(el, url, target, autoplay)
    return true
  }

  /** Run `action` once `el` knows its metadata (immediately if it already does); the latest request wins. */
  private whenMetadataReady(el: HTMLAudioElement, action: () => void): void {
    this.cancelPendingMetadataAction()
    if (metadataReady(el)) {
      action()
      return
    }
    const handler = () => {
      el.removeEventListener("loadedmetadata", handler)
      if (this.pendingMetadataAction?.handler === handler) this.pendingMetadataAction = null
      if (el !== this.el) return
      action()
    }
    this.pendingMetadataAction = { el, handler }
    el.addEventListener("loadedmetadata", handler)
  }

  private cancelPendingMetadataAction(): void {
    const pending = this.pendingMetadataAction
    if (!pending) return
    this.pendingMetadataAction = null
    pending.el.removeEventListener("loadedmetadata", pending.handler)
  }

  /**
   * Decide whether the current track's own buffering has settled, so the
   * next track can be fetched without competing with it. Fired by the first of:
   * fully buffered; the browser idling its download (`suspend`, NETWORK_IDLE)
   * with at least min(SETTLED_AHEAD_SECONDS, remaining) ready ahead; or the
   * safety nets — buffered end at SETTLED_BUFFERED_FRACTION of the track, or
   * fewer than SETTLED_REMAINING_SECONDS left to play. All measured on the
   * track's timeline, so an offset (`t`) stream counts from its offset.
   */
  private checkBufferingSettled(el: HTMLAudioElement, trigger: "progress" | "suspend" | "timeupdate"): void {
    if (el !== this.el || this.bufferSettledReported || this.activeTrackId === null) return
    if (this.activeNetworkUrl === null) return
    const duration = this.trackDuration()
    if (duration === null) return
    const localPosition = Number.isFinite(el.currentTime) ? el.currentTime : 0
    const position = this.streamOffset + localPosition
    const remaining = Math.max(0, duration - position)
    const range = rangeContaining(el.buffered, localPosition)
    const ahead = range ? Math.max(0, range.end - localPosition) : 0
    const bufferedEnd = position + ahead
    let reason: string | null = null
    if (bufferedEnd >= duration - RANGE_TOLERANCE_SECONDS) {
      reason = "fully buffered"
    } else if (
      trigger === "suspend"
      && el.networkState === NETWORK_IDLE
      && el.readyState >= HAVE_FUTURE_DATA
      && ahead >= Math.min(SETTLED_AHEAD_SECONDS, remaining)
    ) {
      reason = "browser stopped buffering"
    } else if (bufferedEnd >= duration * SETTLED_BUFFERED_FRACTION) {
      reason = "buffered past threshold"
    } else if (remaining < SETTLED_REMAINING_SECONDS) {
      reason = "near the end"
    }
    if (reason) this.reportBufferingSettled(reason)
  }

  private reportBufferingSettled(reason: string): void {
    if (this.bufferSettledReported || this.activeTrackId === null) return
    this.bufferSettledReported = true
    playerLog("buffer", "current buffering settled", {
      trackId: this.activeTrackId,
      profile: this.activeProfileKey,
      reason,
    })
    this.callbacks?.onBufferingSettled?.(this.activeTrackId, this.activeProfileKey)
  }

  /** The whole track is local (Blob or a decoded stretch deck). */
  private markFullyLocal(): void {
    if (this.activeTrackId === null) return
    if (!this.fullyLocal) {
      this.fullyLocal = true
      this.callbacks?.onBufferUpdate([{ start: 0, end: 1 }])
    }
    this.reportBufferingSettled("local source")
  }

  private elementForDeck(deck: DeckId): HTMLAudioElement | null {
    if (deck === this.runtime.programDeck) return this.el
    if (this.djDeck?.deck === deck) return this.djDeck.element
    if (this.retired?.deck === deck) return this.retired.element
    return null
  }
}

// Singleton — created once at module load, lives outside React tree
export const playerPlayback = new PlayerPlaybackFacade()
