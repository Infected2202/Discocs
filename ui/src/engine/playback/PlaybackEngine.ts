import { DeckRuntime } from "./DeckRuntime"
import { freeDeck, initialDeckRoleState, reduceDeckRoles, type DeckRoleState } from "./deckRoles"
import { MixerGraph, type MixerGraphLogger } from "./MixerGraph"
import { detectPlaybackCapabilities } from "./capabilities"
import { StretchDeckSource, type StretchDeckSourceDependencies } from "./signalsmith/StretchDeckSource"
import {
  resolveStretchEligibility,
  type StretchEligibilityResolver,
} from "./signalsmith/selection"
import { clampBipolar, clampNormalized } from "./curves"
import { alignFollowerPosition, projectPlayhead, tempoRatioFor } from "./beatSync"
import {
  canBecomeClockMaster,
  canBecomeMaster,
  canEngageBeatSync,
  canEngageTempoSync,
} from "./tempoSync"
import type { SyncMode, TempoSyncState } from "./tempoSync"
import { TempoSyncController, type TempoSyncHost } from "./TempoSyncController"
import { reportEngineFailure } from "./reportEngineFailure"
import type { DecodedTimeline } from "@/engine/timeline/types"
import type {
  DeckId,
  DeckSnapshot,
  DeckSourceUpgradeResult,
  DeckTempoSyncSnapshot,
  EqBand,
  HandoverRequest,
  HandoverResult,
  PlaybackCapabilities,
  PlaybackEngineSnapshot,
  SourceMetadata,
  TempoMaster,
  TempoNudgeDirection,
  TempoSyncSnapshot,
  TrackSource,
  TransportState,
  LoopState,
} from "./types"

// Momentary pitch-bend applied while a TempoSync nudge button is held, as a
// fraction of the locked ratio (2%). Released back to the exact locked ratio
// in endTempoNudge -- this never touches `tempoRatios`, which stays the
// source of truth for the locked rate throughout the nudge.
const TEMPO_NUDGE_FRACTION = 0.02

const emptyDeck = (id: DeckId) => ({
  id,
  role: id === "A" ? "program" as const : "free" as const,
  preparation: id === "A" ? "ready" as const : "empty" as const,
  transport: "idle" as const,
  trackId: null,
  queueItemId: null,
  duration: null,
  anchor: null,
  buffered: [],
  sourceKind: null,
  tempoMode: "unavailable" as const,
  tempoRatio: 1,
  degradedReason: null,
})

const defaultMixerState = () => ({
  crossfader: 0,
  masterGain: 1,
  channelFaders: { A: 1, B: 1 },
  trims: { A: 0.5, B: 0.5 },
  eq: {
    A: { low: 0.5, mid: 0.5, high: 0.5 },
    B: { low: 0.5, mid: 0.5, high: 0.5 },
  },
  filters: { A: 0, B: 0 },
})

function externalTransport(element: HTMLMediaElement): TransportState {
  if (element.error) return "error"
  if (element.ended) return "ended"
  return element.paused ? "paused" : "playing"
}

export class PlaybackEngine implements TempoSyncHost {
  private readonly log: MixerGraphLogger
  private context: AudioContext | null = null
  private graph: MixerGraph | null = null
  private decks: Record<DeckId, DeckRuntime> | null = null
  private externalNodes: Record<DeckId, MediaElementAudioSourceNode | null> = { A: null, B: null }
  private externalElements: Record<DeckId, HTMLMediaElement | null> = { A: null, B: null }
  private externalListenerCleanup: Record<DeckId, (() => void) | null> = { A: null, B: null }
  private readonly generations: Record<DeckId, number> = { A: 0, B: 0 }
  private readonly upgradeGenerations: Record<DeckId, number> = { A: 0, B: 0 }
  private identities: Record<DeckId, { trackId: number | null; queueItemId: string | null }> = {
    A: { trackId: null, queueItemId: null },
    B: { trackId: null, queueItemId: null },
  }
  private roles: DeckRoleState = initialDeckRoleState()
  private revision = 0
  private readonly listeners = new Set<() => void>()
  private mixer = defaultMixerState()
  private readonly stretchDependencies: StretchDeckSourceDependencies
  private readonly stretchEligibility: StretchEligibilityResolver
  private readonly degradedReasons: Record<DeckId, string | null> = { A: null, B: null }
  private readonly tempoRatios: Record<DeckId, number> = { A: 1, B: 1 }
  private readonly timelines: Record<DeckId, DecodedTimeline | null> = { A: null, B: null }
  // Real, engine-owned clock tempo used for actual Web Audio scheduling when
  // master === "clock". Distinct from tempoSync's own `clockBpm`, which R1
  // deliberately leaves static (126) with no mutating command — that field is
  // only a coarse feasibility gate for the reducer's own math, not this.
  private clockBpm = 126
  private readonly tempoSync: TempoSyncController = new TempoSyncController(this)
  private readonly nudging: Record<DeckId, boolean> = { A: false, B: false }

  constructor(
    log: MixerGraphLogger = () => undefined,
    dependencies: {
      readonly stretch?: StretchDeckSourceDependencies
      readonly stretchEligibility?: StretchEligibilityResolver
    } = {},
  ) {
    this.log = log
    this.stretchDependencies = dependencies.stretch ?? {}
    this.stretchEligibility = dependencies.stretchEligibility ?? resolveStretchEligibility
  }

  async ensureReady(): Promise<PlaybackCapabilities> {
    const capabilities = detectPlaybackCapabilities()
    if (capabilities.manualMix) {
      this.initialize()
      const context = this.context
      if (context?.state === "suspended") await context.resume()
      this.graph?.logContextState()
    }
    return capabilities
  }

  routeProgramElement(
    element: HTMLMediaElement,
    trackId: number | null = null,
    queueItemId: string | null = null,
  ): boolean {
    return this.routeElement(this.roles.program, element, "program", trackId, queueItemId)
  }

  routeIncomingElement(
    element: HTMLMediaElement,
    trackId: number | null = null,
    queueItemId: string | null = null,
  ): DeckId | null {
    const deck = freeDeck(this.roles)
    return this.routeElement(deck, element, "prepared", trackId, queueItemId) ? deck : null
  }

  routeElement(
    deck: DeckId,
    element: HTMLMediaElement,
    role: "program" | "prepared",
    trackId: number | null = null,
    queueItemId: string | null = null,
  ): boolean {
    if (this.externalElements[deck] === element) {
      this.identities[deck] = { trackId, queueItemId }
      void this.tempoSync.dispatch({ type: "deck-transport", deck, transport: externalTransport(element) })
      this.changed()
      return true
    }
    ++this.upgradeGenerations[deck]
    this.clearDeckSync(deck)
    const capabilities = detectPlaybackCapabilities()
    if (!capabilities.manualMix) return false
    this.initialize()
    const context = this.context
    const graph = this.graph
    if (!context || !graph) return false

    void this.decks?.[deck].release()
    const node = context.createMediaElementSource(element)
    const previous = this.externalNodes[deck]
    if (previous) graph.detachSource(deck, previous)
    const generation = ++this.generations[deck]
    if (!graph.attachSource(deck, node, generation)) {
      node.disconnect()
      if (previous) graph.attachSource(deck, previous, generation)
      return false
    }
    previous?.disconnect()
    this.externalNodes[deck] = node
    this.watchExternalElement(deck, element)
    this.identities[deck] = { trackId, queueItemId }
    this.degradedReasons[deck] = null
    this.tempoRatios[deck] = element.playbackRate
    void this.tempoSync.dispatch({
      type: "deck-capability", deck, hasTrack: trackId !== null, feasible: false, ready: false, bpm: null,
    })
    void this.tempoSync.dispatch({ type: "deck-transport", deck, transport: externalTransport(element) })
    if (role === "prepared") {
      this.roles = reduceDeckRoles(this.roles, { type: "preparing", deck })
      this.roles = reduceDeckRoles(this.roles, { type: "prepared", deck })
    }
    this.changed()
    return true
  }

  async load(deck: DeckId, source: TrackSource): Promise<SourceMetadata> {
    const capabilities = await this.ensureReady()
    if (!capabilities.manualMix || !this.decks) {
      throw new Error(capabilities.reasons.join("; ") || "Web Audio mixer is unavailable")
    }
    this.decks[deck].advanceGenerationFloor(this.generations[deck])
    const metadata = await this.decks[deck].load(source)
    this.roles = reduceDeckRoles(this.roles, { type: "prepared", deck })
    this.changed()
    return metadata
  }

  async upgradeDeckSource(
    deck: DeckId,
    source: TrackSource,
    options: { readonly startAtSeconds?: number; readonly autoplay?: boolean } = {},
  ): Promise<DeckSourceUpgradeResult> {
    const upgradeGeneration = ++this.upgradeGenerations[deck]
    const currentTempoRatio = this.tempoRatios[deck]
    const capabilities = await this.ensureReady()
    if (!capabilities.manualMix || !this.decks || source.trackId === null) {
      const reason = capabilities.reasons.join("; ") || "Signalsmith requires a persisted track"
      this.degradedReasons[deck] = reason
      void this.tempoSync.dispatch({
        type: "deck-capability", deck, hasTrack: source.trackId !== null, feasible: false, ready: false, bpm: null,
      })
      this.changed()
      return { upgraded: false, kind: "media-element", reason }
    }
    // Mark feasible optimistically as soon as an upgrade attempt is in
    // flight: a SYNC toggle that lands while this await is pending should see
    // "known-feasible, not yet ready" (arming), not "infeasible" (rejected).
    void this.tempoSync.dispatch({
      type: "deck-capability", deck, hasTrack: true, feasible: true, ready: false, bpm: null,
    })
    const eligibility = await this.stretchEligibility(source.trackId)
    if (upgradeGeneration !== this.upgradeGenerations[deck]) {
      return { upgraded: false, kind: "media-element", reason: "Deck source changed" }
    }
    if (!eligibility.ready) {
      this.degradedReasons[deck] = eligibility.reason
      void this.tempoSync.dispatch({
        type: "deck-capability", deck, hasTrack: true, feasible: false, ready: false, bpm: null,
      })
      this.changed()
      return { upgraded: false, kind: "media-element", reason: eligibility.reason }
    }
    this.timelines[deck] = eligibility.timeline ?? null
    try {
      this.decks[deck].advanceGenerationFloor(this.generations[deck])
      await this.decks[deck].load(source, { ...options, tempoRatio: currentTempoRatio })
      if (upgradeGeneration !== this.upgradeGenerations[deck]) {
        await this.decks[deck].release()
        return { upgraded: false, kind: "media-element", reason: "Deck source changed" }
      }
      this.detachExternalRoute(deck)
      this.identities[deck] = { trackId: source.trackId, queueItemId: source.queueItemId ?? null }
      this.degradedReasons[deck] = null
      this.tempoRatios[deck] = currentTempoRatio
      this.roles = reduceDeckRoles(this.roles, { type: "prepared", deck })
      // No explicit master-promotion call here (unlike the old inline
      // `if (options.autoplay && ...)` block this replaces): DeckRuntime.load
      // itself calls its notify callback once activation completes, which
      // flows through handleDeckRuntimeChange -> a deck-transport{playing}
      // dispatch -> the reducer's own auto-promotion. Same mechanism as any
      // other transport change, nothing bespoke needed here.
      void this.tempoSync.dispatch({
        type: "deck-capability", deck, hasTrack: true, feasible: true, ready: true,
        bpm: this.deckEffectiveBpm(deck),
      })
      this.changed()
      return { upgraded: true, kind: "signalsmith", reason: null }
    } catch (error) {
      const reason = error instanceof Error ? error.message : "Signalsmith initialization failed"
      this.degradedReasons[deck] = reason
      // Covers the DeckRuntime.load() timeout and StretchDeckSource's
      // abort-race rejections (SYNC_REWRITE_PLAN.md §2.3/R7) along with every
      // other upgrade failure: the DJ panel's degraded-reason text is the
      // only in-app surface (product decision #4, no toast/banner), so
      // console is the only place this is otherwise observable.
      reportEngineFailure(`PlaybackEngine.upgradeDeckSource(${deck})`, error)
      void this.tempoSync.dispatch({
        type: "deck-capability", deck, hasTrack: true, feasible: false, ready: false, bpm: null,
      })
      this.changed()
      return { upgraded: false, kind: "media-element", reason }
    }
  }

  async playDeck(deck: DeckId, when?: number): Promise<void> {
    const runtime = this.decks?.[deck]
    if (!runtime) throw new Error("Playback engine is not initialized")
    const { effects } = await this.tempoSync.dispatch({ type: "deck-transport", deck, transport: "playing" })
    if (!effects.some((effect) => effect.deck === deck)) {
      await runtime.play(when)
    }
    this.changed()
  }

  async pauseDeck(deck: DeckId, when?: number): Promise<void> {
    const runtime = this.decks?.[deck]
    if (!runtime) throw new Error("Playback engine is not initialized")
    await runtime.pause(when)
    await this.tempoSync.dispatch({ type: "deck-transport", deck, transport: "paused" })
    this.changed()
  }

  async seekDeck(deck: DeckId, seconds: number, when?: number): Promise<void> {
    const runtime = this.decks?.[deck]
    if (!runtime) throw new Error("Playback engine is not initialized")
    const state = this.tempoSync.getState()
    const master = state.master
    const isFollower = this.tempoSync.isFollower(deck)
    const isMaster = this.tempoSync.isMaster(deck)
    const scheduled = (isFollower || (isMaster && this.tempoSync.hasEngagedFollowers()))
      ? when ?? this.syncScheduleTime(deck, master)
      : when

    if (isFollower) {
      await this.alignFollower(deck, master, state.followers[deck].mode, false, seconds)
      this.changed()
      return
    }
    await runtime.seek(seconds, scheduled)
    if (isMaster) await this.cascadeAlignment()
    this.changed()
  }

  async setTempo(deck: DeckId, ratio: number, when?: number): Promise<void> {
    if (!this.tempoSync.isMaster(deck)) throw new Error(`Deck ${deck} tempo is controlled by the tempo master`)
    const clamped = Math.max(0.5, Math.min(2, ratio))
    const runtime = this.decks?.[deck]
    const scheduled = this.tempoSync.hasEngagedFollowers() ? when ?? this.syncScheduleTime(deck, deck) : when
    if (runtime?.sourceKind === "signalsmith") {
      await runtime.setRate(clamped, scheduled)
    } else {
      const element = this.externalElements[deck]
      if (!element) throw new Error(`Deck ${deck} has no loaded source`)
      element.playbackRate = clamped
      element.preservesPitch = true
    }
    this.tempoRatios[deck] = clamped
    this.clockBpm = this.deckEffectiveBpm(deck) ?? this.clockBpm
    // Keep the reducer's feasibility gate fresh: its own tempo-range check
    // reads capabilities[master].bpm, which would otherwise go stale the
    // moment the master's rate changes.
    await this.tempoSync.dispatch({
      type: "deck-capability", deck, hasTrack: true, feasible: true, ready: true,
      bpm: this.deckEffectiveBpm(deck),
    })
    await this.cascadeAlignment()
    this.changed()
  }

  async setLoop(deck: DeckId, loop: LoopState, when?: number): Promise<void> {
    const runtime = this.decks?.[deck]
    if (!runtime || runtime.sourceKind !== "signalsmith") {
      throw new Error(`Deck ${deck} requires Signalsmith for beat-synced loops`)
    }
    const state = this.tempoSync.getState()
    const master = state.master
    const isFollower = this.tempoSync.isFollower(deck)
    const isMaster = this.tempoSync.isMaster(deck)
    const scheduled = (isFollower || (isMaster && this.tempoSync.hasEngagedFollowers()))
      ? when ?? this.syncScheduleTime(deck, master)
      : when
    await runtime.setLoop(loop, scheduled)
    if (isFollower) await this.alignFollower(deck, master, state.followers[deck].mode, false)
    else if (isMaster) await this.cascadeAlignment()
    this.changed()
  }

  async setAutoMaster(): Promise<void> {
    await this.tempoSync.dispatch({ type: "set-auto-master" })
    this.changed()
  }

  async setClockMaster(): Promise<void> {
    if (this.findPlayingDeck()) throw new Error("The master clock is unavailable while a deck is playing")
    await this.tempoSync.dispatch({ type: "set-clock-master" })
    this.changed()
  }

  async setTempoMaster(deck: DeckId): Promise<void> {
    if (this.deckTransport(deck) !== "playing") throw new Error(`Deck ${deck} must be playing to become tempo master`)
    await this.tempoSync.dispatch({ type: "set-deck-master", deck })
    this.changed()
  }

  async setClockTempo(bpm: number): Promise<void> {
    if (!Number.isFinite(bpm) || bpm <= 0) throw new RangeError("Master clock tempo must be positive")
    this.clockBpm = bpm
    if (this.tempoSync.isMaster("clock")) await this.cascadeAlignment()
    this.changed()
  }

  async toggleSync(deck: DeckId, mode: SyncMode): Promise<void> {
    await this.tempoSync.dispatch({ type: "toggle-sync", deck, mode })
    this.changed()
  }

  isStretchDeck(deck: DeckId): boolean {
    return this.decks?.[deck].sourceKind === "signalsmith"
  }

  async unload(deck: DeckId): Promise<void> {
    await this.decks?.[deck].release()
    this.detachExternal(deck)
    this.roles = reduceDeckRoles(this.roles, { type: "retire", deck })
    this.clearDeckSync(deck)
    await this.tempoSync.dispatch({ type: "deck-transport", deck, transport: "idle" })
    this.changed()
  }

  async handover(request: HandoverRequest): Promise<HandoverResult> {
    await this.ensureReady()
    if (request.incomingDeck === this.roles.program || this.roles.preparation[request.incomingDeck] !== "ready") {
      throw new Error("Incoming deck is not ready")
    }
    const outgoingDeck = this.roles.program
    this.setCrossfader(request.incomingDeck === "A" ? -1 : 1)
    this.roles = reduceDeckRoles(this.roles, { type: "handover", incoming: request.incomingDeck })
    this.changed()
    return { outgoingDeck, programDeck: request.incomingDeck, clientHandoverId: request.clientHandoverId }
  }

  async confirmRetirement(deck: DeckId): Promise<void> {
    if (deck === this.roles.program) throw new Error("Cannot retire the program deck")
    await this.unload(deck)
  }

  cancelIncoming(): void {
    const deck = freeDeck(this.roles)
    ++this.upgradeGenerations[deck]
    void this.decks?.[deck].release()
    this.detachExternal(deck)
    this.roles = reduceDeckRoles(this.roles, { type: "cancel-preparation", deck })
    this.changed()
  }

  setTrim(deck: DeckId, value: number, when?: number): void {
    this.requireGraph().setTrim(deck, value, when)
    this.mixer.trims[deck] = clampNormalized(value)
    this.changed()
  }

  setEq(deck: DeckId, band: EqBand, value: number, when?: number): void {
    this.requireGraph().setEq(deck, band, value, when)
    this.mixer.eq[deck][band] = clampNormalized(value)
    this.changed()
  }

  setFilter(deck: DeckId, value: number, when?: number): void {
    this.requireGraph().setFilter(deck, value, when)
    this.mixer.filters[deck] = clampBipolar(value)
    this.changed()
  }

  setChannelFader(deck: DeckId, value: number, when?: number): void {
    this.requireGraph().setChannelFader(deck, value, when)
    this.mixer.channelFaders[deck] = clampNormalized(value)
    this.changed()
  }

  setCrossfader(value: number, when?: number): void {
    this.requireGraph().setCrossfader(value, when)
    this.mixer.crossfader = clampBipolar(value)
    this.changed()
  }

  setMasterGain(value: number, when?: number): void {
    this.requireGraph().setMasterGain(value, when)
    this.mixer.masterGain = clampNormalized(value)
    this.changed()
  }

  get programDeck(): DeckId {
    return this.roles.program
  }

  get incomingDeck(): DeckId {
    return freeDeck(this.roles)
  }

  getSnapshot(): PlaybackEngineSnapshot {
    const capabilities = detectPlaybackCapabilities()
    const decks: Record<DeckId, DeckSnapshot> = {
      A: this.deckSnapshot("A"),
      B: this.deckSnapshot("B"),
    }
    return {
      revision: this.revision,
      contextState: this.context?.state ?? "uninitialized",
      programDeck: this.roles.program,
      decks,
      mixer: { ...this.mixer, meters: this.getMeterLevels() },
      tempoSync: this.buildTempoSyncSnapshot(),
      automation: { owner: "none" },
      capabilities,
      error: null,
    }
  }

  private buildTempoSyncSnapshot(): TempoSyncSnapshot {
    const state = this.tempoSync.getState()
    return {
      auto: state.auto,
      master: state.master,
      clockBpm: this.currentMasterBpm(),
      canBecomeClockMaster: canBecomeClockMaster(state),
      decks: {
        A: this.deckTempoSyncSnapshot(state, "A"),
        B: this.deckTempoSyncSnapshot(state, "B"),
      },
    }
  }

  private deckTempoSyncSnapshot(state: TempoSyncState, deck: DeckId): DeckTempoSyncSnapshot {
    const follower = state.followers[deck]
    return {
      enabled: follower.enabled,
      mode: follower.mode,
      phase: follower.phase,
      reason: follower.reason,
      phaseOffsetBeats: follower.phaseOffsetBeats,
      canEngageBeatSync: canEngageBeatSync(state, deck),
      canEngageTempoSync: canEngageTempoSync(state, deck),
      canBecomeMaster: canBecomeMaster(state, deck),
    }
  }

  getMeterLevels(): Record<DeckId | "master", number> {
    return this.graph?.readMeters() ?? { A: 0, B: 0, master: 0 }
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  async destroy(): Promise<void> {
    const decks = this.decks
    this.decks = null
    if (decks) await Promise.all([decks.A.release(), decks.B.release()])
    this.detachExternal("A")
    this.detachExternal("B")
    this.graph?.destroy()
    this.graph = null
    const context = this.context
    this.context = null
    if (context && context.state !== "closed") await context.close()
    this.roles = initialDeckRoleState()
    this.mixer = defaultMixerState()
    this.degradedReasons.A = null
    this.degradedReasons.B = null
    this.tempoRatios.A = 1
    this.tempoRatios.B = 1
    this.timelines.A = null
    this.timelines.B = null
    this.clockBpm = 126
    this.tempoSync.reset()
    this.changed()
  }

  private initialize(): void {
    if (this.context) return
    this.context = new AudioContext()
    this.graph = new MixerGraph(this.context, this.log)
    this.graph.setCrossfader(this.mixer.crossfader)
    this.decks = {
      A: new DeckRuntime(
        "A", this.graph,
        () => new StretchDeckSource(this.context!, this.stretchDependencies),
        () => this.handleDeckRuntimeChange("A"),
        undefined,
        () => this.dispatchPhaseOffsetObserved("A"),
      ),
      B: new DeckRuntime(
        "B", this.graph,
        () => new StretchDeckSource(this.context!, this.stretchDependencies),
        () => this.handleDeckRuntimeChange("B"),
        undefined,
        () => this.dispatchPhaseOffsetObserved("B"),
      ),
    }
  }

  private detachExternal(deck: DeckId): void {
    this.detachExternalRoute(deck)
    this.identities[deck] = { trackId: null, queueItemId: null }
  }

  private clearDeckSync(deck: DeckId): void {
    this.timelines[deck] = null
    const follower = this.tempoSync.getState().followers[deck]
    if (follower.enabled) {
      void this.tempoSync.dispatch({ type: "toggle-sync", deck, mode: follower.mode })
    }
    void this.tempoSync.dispatch({
      type: "deck-capability", deck, hasTrack: false, feasible: false, ready: false, bpm: null,
    })
  }

  private hasBeatTimeline(deck: DeckId): boolean {
    return (this.timelines[deck]?.beats.length ?? 0) >= 2
  }

  private findPlayingDeck(exclude?: DeckId): DeckId | null {
    for (const deck of ["A", "B"] as const) {
      if (deck !== exclude && this.deckTransport(deck) === "playing") return deck
    }
    return null
  }

  private deckTransport(deck: DeckId): TransportState {
    const runtime = this.decks?.[deck]
    if (runtime?.sourceKind) return runtime.currentTransport
    const element = this.externalElements[deck]
    return element ? externalTransport(element) : "idle"
  }

  private currentMasterBpm(): number {
    return this.masterTempoFor(this.tempoSync.getState().master)
  }

  private masterTempoFor(master: TempoMaster): number {
    return master === "clock" ? this.clockBpm : this.deckEffectiveBpm(master) ?? this.clockBpm
  }

  private deckEffectiveBpm(deck: DeckId): number | null {
    const bpm = this.timelines[deck]?.bpm
    return bpm && bpm > 0 ? bpm * this.tempoRatios[deck] : null
  }

  private syncUnavailableReason(deck: DeckId, master: TempoMaster): string | null {
    if (!this.hasBeatTimeline(deck)) return `Deck ${deck} requires a beat timeline`
    if (this.decks?.[deck].sourceKind !== "signalsmith") return `Deck ${deck} requires Signalsmith for tempo sync`
    if (master !== "clock" && !this.hasBeatTimeline(master)) {
      return `Tempo master Deck ${master} requires a beat timeline`
    }
    const target = tempoRatioFor(this.masterTempoFor(master), this.timelines[deck]!.bpm)
    return target === null ? "Master tempo is outside the ±8% deck range" : null
  }

  /**
   * Re-aligns every deck currently engaged as a follower of the live master,
   * for master-side mutations (tempo/seek/loop) that R1's reducer has no
   * fact/command for — these are host-level Web Audio orchestration, not
   * reducer decisions, so they bypass tempoSync.dispatch entirely. Failures
   * are reported per-deck and do not abort the others, matching the old
   * Promise.all + per-deck catch behavior; unlike a dispatch-driven effect,
   * a failure here is not fed back as an align-result fact, so a follower's
   * snapshot phase can go stale (still "aligned") if this silently fails.
   */
  private async cascadeAlignment(): Promise<void> {
    const state = this.tempoSync.getState()
    const master = state.master
    await Promise.all((["A", "B"] as const)
      .filter((deck) => this.tempoSync.isFollower(deck))
      .map(async (deck) => {
        try {
          await this.alignFollower(deck, master, state.followers[deck].mode, false)
        } catch (error) {
          reportEngineFailure(`PlaybackEngine.cascadeAlignment(${deck})`, error)
        }
      }))
  }

  /**
   * The TempoSyncHost seam: the only place that turns a tempoSync decision
   * into a real Web Audio operation. Called either by TempoSyncController in
   * response to an align-follower effect, or directly by PlaybackEngine for
   * master-side mutations (see cascadeAlignment) that have no reducer
   * representation. Throws on failure instead of touching sync state itself
   * -- the controller is the only thing that translates a throw into an
   * align-result fact.
   */
  async alignFollower(
    deck: DeckId,
    master: TempoMaster,
    mode: SyncMode,
    start: boolean,
    positionHint?: number,
  ): Promise<void> {
    const runtime = this.decks?.[deck]
    const timeline = this.timelines[deck]
    const reason = this.syncUnavailableReason(deck, master)
    if (!runtime || !timeline || reason) {
      throw new Error(reason ?? "Beat sync is unavailable")
    }
    const ratio = tempoRatioFor(this.masterTempoFor(master), timeline.bpm)
    if (ratio === null) throw new Error("Master tempo is outside the ±8% deck range")
    const scheduled = this.syncScheduleTime(deck, master)

    // TempoSync (SYNC_REWRITE_PLAN.md §2.1): tempo lock only -- phase is
    // allowed to drift by design, corrected manually via beginTempoNudge/
    // endTempoNudge instead. The alignment path here never seeks; starting
    // playback (if requested) begins from wherever the deck already sits
    // rather than snapping to a beat-aligned position, unlike BeatSync below.
    if (mode === "tempo") {
      await runtime.setRate(ratio, scheduled)
      this.tempoRatios[deck] = ratio
      if (start) await runtime.play(scheduled)
      this.changed()
      return
    }

    const followerAnchor = runtime.anchor
    if (!followerAnchor) throw new Error(`Deck ${deck} has no playback clock`)
    const followerPosition = positionHint ?? projectPlayhead(
      followerAnchor.mediaSeconds,
      followerAnchor.audioTime,
      followerAnchor.rate,
      scheduled,
    )
    const masterPhase = this.masterPhaseAt(master, scheduled)
    const target = alignFollowerPosition(
      new Float32Array([0, 1]),
      masterPhase,
      timeline.beats,
      followerPosition,
    )
    if (target === null) throw new Error("Beat phase cannot be mapped")

    await runtime.setRate(ratio, scheduled)
    await runtime.seek(target, scheduled)
    this.tempoRatios[deck] = ratio
    if (start) await runtime.play(scheduled, target)
    this.changed()
  }

  /**
   * Manual pitch-bend correction for an engaged TempoSync follower
   * (SYNC_REWRITE_PLAN.md §2.1): a live `setRate` nudge while held, snapping
   * back to the locked ratio on release. No-op (reported, not thrown -- this
   * is invoked directly from UI press handlers with no call site expecting
   * to catch) for any deck that is not currently an engaged TempoSync
   * follower, matching the reportEngineFailure convention established in
   * R2/R7 for failures with no other user-facing surface.
   */
  beginTempoNudge(deck: DeckId, direction: TempoNudgeDirection): void {
    const runtime = this.decks?.[deck]
    if (!this.isEngagedTempoSyncFollower(deck) || !runtime || runtime.sourceKind !== "signalsmith") {
      reportEngineFailure(
        `PlaybackEngine.beginTempoNudge(${deck})`,
        new Error(`Deck ${deck} is not an engaged TempoSync follower`),
      )
      return
    }
    this.nudging[deck] = true
    const nudgedRatio = this.tempoRatios[deck] * (1 + (direction === "up" ? 1 : -1) * TEMPO_NUDGE_FRACTION)
    void runtime.setRate(nudgedRatio).catch((error) => reportEngineFailure(`PlaybackEngine.beginTempoNudge(${deck})`, error))
    this.changed()
  }

  endTempoNudge(deck: DeckId): void {
    if (!this.nudging[deck]) return
    this.nudging[deck] = false
    const runtime = this.decks?.[deck]
    if (runtime) {
      void runtime.setRate(this.tempoRatios[deck]).catch((error) => reportEngineFailure(`PlaybackEngine.endTempoNudge(${deck})`, error))
    }
    this.changed()
  }

  private isEngagedTempoSyncFollower(deck: DeckId): boolean {
    const follower = this.tempoSync.getState().followers[deck]
    return this.tempoSync.isFollower(deck) && follower.mode === "tempo" && follower.enabled
  }

  private masterPhaseAt(master: TempoMaster, audioTime: number): number {
    if (master === "clock") {
      return ((audioTime * this.clockBpm / 60) % 1 + 1) % 1
    }
    const anchor = this.deckSnapshot(master).anchor
    if (!anchor) return 0
    const position = projectPlayhead(anchor.mediaSeconds, anchor.audioTime, anchor.rate, audioTime)
    return this.phaseForDeckPosition(master, position)
  }

  private phaseForDeckPosition(deck: DeckId, position: number): number {
    const timeline = this.timelines[deck]
    if (!timeline) return 0
    const beats = timeline.beats
    if (beats.length < 2) return 0
    let low = 0
    while (low + 1 < beats.length && beats[low + 1] <= position) low += 1
    const index = Math.min(low, beats.length - 2)
    const width = beats[index + 1] - beats[index]
    return width > 0 ? Math.min(1, Math.max(0, (position - beats[index]) / width)) : 0
  }

  private syncScheduleTime(deck: DeckId, master: TempoMaster): number {
    const contextTime = this.context?.currentTime ?? 0
    const followerLead = this.decks?.[deck].schedulingLeadSeconds ?? 0
    const masterLead = master === "clock" ? 0 : this.decks?.[master].schedulingLeadSeconds ?? 0
    return contextTime + Math.max(followerLead, masterLead) + 0.02
  }

  private detachExternalRoute(deck: DeckId): void {
    this.externalListenerCleanup[deck]?.()
    this.externalListenerCleanup[deck] = null
    this.externalElements[deck] = null
    const node = this.externalNodes[deck]
    if (!node) return
    this.graph?.detachSource(deck, node)
    node.disconnect()
    this.externalNodes[deck] = null
  }

  private deckSnapshot(deck: DeckId): DeckSnapshot {
    const snapshot: DeckSnapshot = {
      ...emptyDeck(deck),
      role: this.roles.roles[deck],
      preparation: this.roles.preparation[deck],
      trackId: this.identities[deck].trackId,
      queueItemId: this.identities[deck].queueItemId,
    }
    const element = this.externalElements[deck]
    const runtime = this.decks?.[deck]
    if (runtime?.sourceKind) {
      snapshot.transport = runtime.currentTransport
      snapshot.duration = runtime.sourceDuration
      snapshot.anchor = runtime.anchor
      snapshot.buffered = runtime.buffered
      snapshot.sourceKind = runtime.sourceKind
      snapshot.tempoMode = "pitch-preserving"
      snapshot.tempoRatio = this.tempoRatios[deck]
      snapshot.degradedReason = this.degradedReasons[deck]
      return snapshot
    }
    if (!element) {
      snapshot.degradedReason = this.degradedReasons[deck]
      return snapshot
    }
    snapshot.transport = externalTransport(element)
    snapshot.duration = Number.isFinite(element.duration) ? element.duration : null
    snapshot.anchor = {
      mediaSeconds: element.currentTime,
      audioTime: this.context?.currentTime ?? 0,
      rate: element.playbackRate,
    }
    snapshot.sourceKind = "media-element"
    snapshot.tempoMode = "native"
    snapshot.tempoRatio = element.playbackRate
    snapshot.degradedReason = this.degradedReasons[deck]
    return snapshot
  }

  private watchExternalElement(deck: DeckId, element: HTMLMediaElement): void {
    this.externalListenerCleanup[deck]?.()
    const notify = () => {
      void this.tempoSync.dispatch({ type: "deck-transport", deck, transport: externalTransport(element) })
      this.changed()
    }
    const events = [
      "play", "playing", "pause", "waiting", "ended", "error", "loadedmetadata", "seeked", "ratechange",
    ] as const
    events.forEach((event) => element.addEventListener(event, notify))
    this.externalElements[deck] = element
    this.externalListenerCleanup[deck] = () => {
      events.forEach((event) => element.removeEventListener(event, notify))
    }
  }

  private handleDeckRuntimeChange(deck: DeckId): void {
    void this.tempoSync.dispatch({ type: "deck-transport", deck, transport: this.deckTransport(deck) })
    this.changed()
  }

  /**
   * SYNC_REWRITE_PLAN.md §2.1: wired as DeckRuntime's dedicated onClockTick
   * callback (StretchDeckSource's setUpdateInterval -> handleClockUpdate ->
   * a channel separate from the general notify used for transport/seek/
   * rate changes), so this only ever runs from the real ~0.25s Signalsmith
   * clock tick -- never spuriously from an unrelated play/pause/seek/setRate
   * call, which would risk measuring transient scheduling-lead noise as
   * "drift" and re-aligning a follower that never actually needed it.
   */
  private dispatchPhaseOffsetObserved(deck: DeckId): void {
    const state = this.tempoSync.getState()
    const follower = state.followers[deck]
    if (!follower.enabled || deck === state.master) return
    // A paused follower isn't drifting from the master in any meaningful
    // sense -- its own worklet clock tick can still fire while paused (the
    // Signalsmith node's periodic timer isn't itself gated on transport
    // state), but measuring "drift" against a frozen, not-currently-playing
    // position would be nonsensical and could spuriously re-trigger
    // alignment on a deck the user deliberately paused.
    if (this.deckTransport(deck) !== "playing") return
    const offsetBeats = this.computePhaseOffsetBeats(deck, state.master)
    if (offsetBeats === null) return
    void this.tempoSync.dispatch({
      type: "phase-offset-observed", deck, offsetBeats, at: this.context?.currentTime ?? 0,
    })
  }

  private computePhaseOffsetBeats(deck: DeckId, master: TempoMaster): number | null {
    const runtime = this.decks?.[deck]
    const timeline = this.timelines[deck]
    const anchor = runtime?.anchor
    if (!runtime || !timeline || !anchor || timeline.beats.length < 2) return null
    const audioTime = this.context?.currentTime ?? 0
    const followerPosition = projectPlayhead(anchor.mediaSeconds, anchor.audioTime, anchor.rate, audioTime)
    const followerPhase = this.phaseForDeckPosition(deck, followerPosition)
    const masterPhase = this.masterPhaseAt(master, audioTime)
    const diff = followerPhase - masterPhase
    // Wrap to a signed nearest-wraparound offset (roughly -0.5 to +0.5 beats):
    // the reducer's drift threshold/cooldown expects that, not a raw phase
    // difference that could jump straight to ~1 at the wrap boundary.
    return diff - Math.round(diff)
  }

  private requireGraph(): MixerGraph {
    if (!this.graph) throw new Error("Playback engine is not initialized")
    return this.graph
  }

  private changed(): void {
    this.revision += 1
    this.listeners.forEach((listener) => listener())
  }
}
