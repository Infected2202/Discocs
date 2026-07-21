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
import type { DecodedTimeline } from "@/engine/timeline/types"
import type {
  DeckId,
  BeatSyncSnapshot,
  DeckSnapshot,
  DeckSourceUpgradeResult,
  EqBand,
  HandoverRequest,
  HandoverResult,
  PlaybackCapabilities,
  PlaybackEngineSnapshot,
  SourceMetadata,
  TempoMaster,
  TrackSource,
  TransportState,
  LoopState,
} from "./types"

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

export class PlaybackEngine {
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
  private beatSync: BeatSyncSnapshot = {
    auto: true,
    master: "clock" as TempoMaster,
    clockBpm: 126,
    decks: {
      A: { enabled: false, phase: "off" as const, reason: null as string | null },
      B: { enabled: false, phase: "off" as const, reason: null as string | null },
    },
  }

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
      this.reconcileAutoMaster(deck)
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
    this.reconcileAutoMaster(deck)
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
    const capabilities = await this.ensureReady()
    if (!capabilities.manualMix || !this.decks || source.trackId === null) {
      const reason = capabilities.reasons.join("; ") || "Signalsmith requires a persisted track"
      this.degradedReasons[deck] = reason
      this.changed()
      return { upgraded: false, kind: "media-element", reason }
    }
    const eligibility = await this.stretchEligibility(source.trackId)
    if (upgradeGeneration !== this.upgradeGenerations[deck]) {
      return { upgraded: false, kind: "media-element", reason: "Deck source changed" }
    }
    if (!eligibility.ready) {
      this.degradedReasons[deck] = eligibility.reason
      this.changed()
      return { upgraded: false, kind: "media-element", reason: eligibility.reason }
    }
    this.timelines[deck] = eligibility.timeline ?? null
    this.reconcileAutoMaster(deck)
    try {
      this.decks[deck].advanceGenerationFloor(this.generations[deck])
      await this.decks[deck].load(source, options)
      if (upgradeGeneration !== this.upgradeGenerations[deck]) {
        await this.decks[deck].release()
        return { upgraded: false, kind: "media-element", reason: "Deck source changed" }
      }
      this.detachExternalRoute(deck)
      this.identities[deck] = { trackId: source.trackId, queueItemId: source.queueItemId ?? null }
      this.degradedReasons[deck] = null
      this.tempoRatios[deck] = 1
      this.roles = reduceDeckRoles(this.roles, { type: "prepared", deck })
      if (options.autoplay && this.beatSync.master === "clock") {
        this.beatSync.auto = true
        this.assignTempoMaster(deck)
      }
      this.changed()
      return { upgraded: true, kind: "signalsmith", reason: null }
    } catch (error) {
      const reason = error instanceof Error ? error.message : "Signalsmith initialization failed"
      this.degradedReasons[deck] = reason
      this.changed()
      return { upgraded: false, kind: "media-element", reason }
    }
  }

  async playDeck(deck: DeckId, when?: number): Promise<void> {
    const runtime = this.decks?.[deck]
    if (!runtime) throw new Error("Playback engine is not initialized")
    if (this.beatSync.master === "clock") {
      this.beatSync.auto = true
      this.assignTempoMaster(deck)
    }
    if (this.isSyncedFollower(deck)) {
      await this.synchronizeFollower(deck, true, when)
      return
    }
    await runtime.play(when)
    this.changed()
  }

  async pauseDeck(deck: DeckId, when?: number): Promise<void> {
    const runtime = this.decks?.[deck]
    if (!runtime) throw new Error("Playback engine is not initialized")
    await runtime.pause(when)
    if (this.beatSync.master === deck) {
      this.assignTempoMaster(this.findPlayingDeck(deck) ?? "clock")
      await this.synchronizeFollowers()
    }
  }

  async seekDeck(deck: DeckId, seconds: number, when?: number): Promise<void> {
    const runtime = this.decks?.[deck]
    if (!runtime) throw new Error("Playback engine is not initialized")
    const scheduled = (this.isSyncedFollower(deck) || (this.beatSync.master === deck && this.hasSyncedFollowers()))
      ? when ?? this.syncScheduleTime(deck)
      : when
    if (this.isSyncedFollower(deck)) {
      await this.synchronizeFollower(deck, false, scheduled, seconds)
      return
    }
    await runtime.seek(seconds, scheduled)
    if (this.beatSync.master === deck) {
      await this.synchronizeFollowers(scheduled, this.phaseForDeckPosition(deck, seconds))
    }
  }

  async setTempo(deck: DeckId, ratio: number, when?: number): Promise<void> {
    if (this.beatSync.master !== deck) throw new Error(`Deck ${deck} tempo is controlled by the tempo master`)
    const clamped = Math.max(0.5, Math.min(2, ratio))
    const runtime = this.decks?.[deck]
    const scheduled = this.beatSync.master === deck && this.hasSyncedFollowers()
      ? when ?? this.syncScheduleTime(deck)
      : when
    if (runtime?.sourceKind === "signalsmith") {
      await runtime.setRate(clamped, scheduled)
    } else {
      const element = this.externalElements[deck]
      if (!element) throw new Error(`Deck ${deck} has no loaded source`)
      element.playbackRate = clamped
      element.preservesPitch = true
    }
    this.tempoRatios[deck] = clamped
    if (this.beatSync.master === deck) {
      this.beatSync.clockBpm = this.deckEffectiveBpm(deck) ?? this.beatSync.clockBpm
      await this.synchronizeFollowers(scheduled)
    }
    this.changed()
  }

  async setLoop(deck: DeckId, loop: LoopState, when?: number): Promise<void> {
    const runtime = this.decks?.[deck]
    if (!runtime || runtime.sourceKind !== "signalsmith") {
      throw new Error(`Deck ${deck} requires Signalsmith for beat-synced loops`)
    }
    const scheduled = (this.isSyncedFollower(deck) || (this.beatSync.master === deck && this.hasSyncedFollowers()))
      ? when ?? this.syncScheduleTime(deck)
      : when
    await runtime.setLoop(loop, scheduled)
    if (this.isSyncedFollower(deck)) await this.synchronizeFollower(deck, false, scheduled)
    else if (this.beatSync.master === deck) await this.synchronizeFollowers(scheduled)
  }

  async setAutoMaster(): Promise<void> {
    this.beatSync.auto = true
    this.assignTempoMaster(this.findPlayingDeck() ?? "clock")
    await this.synchronizeFollowers()
    this.changed()
  }

  async setClockMaster(): Promise<void> {
    if (this.findPlayingDeck()) throw new Error("The master clock is unavailable while a deck is playing")
    this.beatSync.auto = false
    this.assignTempoMaster("clock")
    await this.synchronizeFollowers()
    this.changed()
  }

  async setTempoMaster(deck: DeckId): Promise<void> {
    if (this.deckTransport(deck) !== "playing") throw new Error(`Deck ${deck} must be playing to become tempo master`)
    this.beatSync.auto = true
    this.assignTempoMaster(deck)
    await this.synchronizeFollowers()
    this.changed()
  }

  async setClockTempo(bpm: number): Promise<void> {
    if (!Number.isFinite(bpm) || bpm <= 0) throw new RangeError("Master clock tempo must be positive")
    this.beatSync.clockBpm = bpm
    if (this.beatSync.master === "clock") await this.synchronizeFollowers()
    this.changed()
  }

  async toggleSync(deck: DeckId): Promise<void> {
    const state = this.beatSync.decks[deck]
    if (state.enabled) {
      state.enabled = false
      state.phase = "off"
      state.reason = null
      this.changed()
      return
    }
    if (this.beatSync.master === deck) {
      state.enabled = true
      state.phase = "aligned"
      state.reason = null
      this.changed()
      return
    }
    const reason = this.syncUnavailableReason(deck)
    if (reason) {
      state.phase = "unavailable"
      state.reason = reason
      this.changed()
      throw new Error(reason)
    }
    state.enabled = true
    state.phase = "pending"
    state.reason = null
    try {
      await this.synchronizeFollower(deck, false)
      this.changed()
    } catch (error) {
      state.enabled = false
      state.phase = "unavailable"
      state.reason = error instanceof Error ? error.message : "Beat sync failed"
      this.changed()
      throw error
    }
  }

  isStretchDeck(deck: DeckId): boolean {
    return this.decks?.[deck].sourceKind === "signalsmith"
  }

  async unload(deck: DeckId): Promise<void> {
    await this.decks?.[deck].release()
    this.detachExternal(deck)
    this.roles = reduceDeckRoles(this.roles, { type: "retire", deck })
    this.clearDeckSync(deck)
    if (this.beatSync.master === deck) {
      this.assignTempoMaster(this.findPlayingDeck(deck) ?? "clock")
      await this.synchronizeFollowers()
    }
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
      beatSync: {
        auto: this.beatSync.auto,
        master: this.beatSync.master,
        clockBpm: this.currentMasterBpm(),
        decks: {
          A: { ...this.beatSync.decks.A },
          B: { ...this.beatSync.decks.B },
        },
      },
      automation: { owner: "none" },
      capabilities,
      error: null,
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
    this.beatSync = {
      auto: true,
      master: "clock",
      clockBpm: 126,
      decks: {
        A: { enabled: false, phase: "off", reason: null },
        B: { enabled: false, phase: "off", reason: null },
      },
    }
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
      ),
      B: new DeckRuntime(
        "B", this.graph,
        () => new StretchDeckSource(this.context!, this.stretchDependencies),
        () => this.handleDeckRuntimeChange("B"),
      ),
    }
  }

  private detachExternal(deck: DeckId): void {
    this.detachExternalRoute(deck)
    this.identities[deck] = { trackId: null, queueItemId: null }
  }

  private clearDeckSync(deck: DeckId): void {
    this.timelines[deck] = null
    this.beatSync.decks[deck] = { enabled: false, phase: "off", reason: null }
  }

  private hasBeatTimeline(deck: DeckId): boolean {
    return (this.timelines[deck]?.beats.length ?? 0) >= 2
  }

  private isSyncedFollower(deck: DeckId): boolean {
    return this.beatSync.master !== deck && this.beatSync.decks[deck].enabled
  }

  private hasSyncedFollowers(): boolean {
    return (["A", "B"] as const).some((deck) => this.isSyncedFollower(deck))
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

  private assignTempoMaster(master: TempoMaster): void {
    this.beatSync.master = master
    if (master !== "clock") {
      const bpm = this.deckEffectiveBpm(master)
      if (bpm !== null) this.beatSync.clockBpm = bpm
    }
  }

  private currentMasterBpm(): number {
    return this.beatSync.master === "clock"
      ? this.beatSync.clockBpm
      : this.deckEffectiveBpm(this.beatSync.master) ?? this.beatSync.clockBpm
  }

  private deckEffectiveBpm(deck: DeckId): number | null {
    const bpm = this.timelines[deck]?.bpm
    return bpm && bpm > 0 ? bpm * this.tempoRatios[deck] : null
  }

  private syncUnavailableReason(deck: DeckId): string | null {
    if (!this.hasBeatTimeline(deck)) return `Deck ${deck} requires a beat timeline`
    if (this.decks?.[deck].sourceKind !== "signalsmith") return `Deck ${deck} requires Signalsmith for tempo sync`
    if (this.beatSync.master !== "clock" && !this.hasBeatTimeline(this.beatSync.master)) {
      return `Tempo master Deck ${this.beatSync.master} requires a beat timeline`
    }
    const target = tempoRatioFor(this.currentMasterBpm(), this.timelines[deck]!.bpm)
    return target === null ? "Master tempo is outside the ±8% deck range" : null
  }

  private async synchronizeFollowers(when?: number, masterPhase?: number): Promise<void> {
    await Promise.all((["A", "B"] as const)
      .filter((deck) => this.isSyncedFollower(deck))
      .map((deck) => this.synchronizeFollower(deck, false, when, undefined, masterPhase)))
  }

  private async synchronizeFollower(
    deck: DeckId,
    start: boolean,
    when?: number,
    positionHint?: number,
    masterPhaseHint?: number,
  ): Promise<void> {
    const runtime = this.decks?.[deck]
    const timeline = this.timelines[deck]
    const state = this.beatSync.decks[deck]
    const reason = this.syncUnavailableReason(deck)
    if (!runtime || !timeline || reason) {
      const unavailable = reason ?? "Beat sync is unavailable"
      state.phase = "unavailable"
      state.reason = unavailable
      if (start) throw new Error(unavailable)
      return
    }
    const ratio = tempoRatioFor(this.currentMasterBpm(), timeline.bpm)!
    const scheduled = when ?? this.syncScheduleTime(deck)
    const followerAnchor = runtime.anchor
    if (!followerAnchor) throw new Error(`Deck ${deck} has no playback clock`)
    const followerPosition = positionHint ?? projectPlayhead(
      followerAnchor.mediaSeconds,
      followerAnchor.audioTime,
      followerAnchor.rate,
      scheduled,
    )
    const masterPhase = masterPhaseHint ?? this.masterPhaseAt(scheduled)
    const target = alignFollowerPosition(
      new Float32Array([0, 1]),
      masterPhase,
      timeline.beats,
      followerPosition,
    )
    if (target === null) throw new Error("Beat phase cannot be mapped")

    state.phase = "pending"
    state.reason = null
    await runtime.setRate(ratio, scheduled)
    await runtime.seek(target, scheduled)
    this.tempoRatios[deck] = ratio
    if (start) await runtime.play(scheduled, target)
    state.phase = "aligned"
    this.changed()
  }

  private masterPhaseAt(audioTime: number): number {
    if (this.beatSync.master === "clock") {
      return ((audioTime * this.beatSync.clockBpm / 60) % 1 + 1) % 1
    }
    const deck = this.beatSync.master
    const anchor = this.deckSnapshot(deck).anchor
    if (!anchor) return 0
    const position = projectPlayhead(anchor.mediaSeconds, anchor.audioTime, anchor.rate, audioTime)
    return this.phaseForDeckPosition(deck, position)
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

  private syncScheduleTime(deck: DeckId): number {
    const contextTime = this.context?.currentTime ?? 0
    const followerLead = this.decks?.[deck].schedulingLeadSeconds ?? 0
    const masterLead = this.beatSync.master === "clock"
      ? 0
      : this.decks?.[this.beatSync.master].schedulingLeadSeconds ?? 0
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
      this.reconcileAutoMaster(deck)
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

  private reconcileAutoMaster(changedDeck: DeckId): void {
    if (this.beatSync.master === "clock") {
      if (
        this.deckTransport(changedDeck) === "playing"
      ) {
        this.beatSync.auto = true
        this.assignTempoMaster(changedDeck)
        void this.synchronizeFollowers().catch(() => undefined)
      }
      return
    }
    if (this.beatSync.master === changedDeck && this.deckTransport(changedDeck) !== "playing") {
      this.assignTempoMaster(this.findPlayingDeck(changedDeck) ?? "clock")
      void this.synchronizeFollowers().catch(() => undefined)
    }
  }

  private handleDeckRuntimeChange(deck: DeckId): void {
    this.reconcileAutoMaster(deck)
    this.changed()
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
