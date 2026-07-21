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
import type {
  DeckId,
  DeckSnapshot,
  DeckSourceUpgradeResult,
  EqBand,
  HandoverRequest,
  HandoverResult,
  PlaybackCapabilities,
  PlaybackEngineSnapshot,
  SourceMetadata,
  TrackSource,
  TransportState,
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
    ++this.upgradeGenerations[deck]
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
    await runtime.play(when)
  }

  async pauseDeck(deck: DeckId, when?: number): Promise<void> {
    const runtime = this.decks?.[deck]
    if (!runtime) throw new Error("Playback engine is not initialized")
    await runtime.pause(when)
  }

  async seekDeck(deck: DeckId, seconds: number): Promise<void> {
    const runtime = this.decks?.[deck]
    if (!runtime) throw new Error("Playback engine is not initialized")
    await runtime.seek(seconds)
  }

  async setTempo(deck: DeckId, ratio: number, when?: number): Promise<void> {
    const clamped = Math.max(0.5, Math.min(2, ratio))
    const runtime = this.decks?.[deck]
    if (runtime?.sourceKind === "signalsmith") {
      await runtime.setRate(clamped, when)
    } else {
      const element = this.externalElements[deck]
      if (!element) throw new Error(`Deck ${deck} has no loaded source`)
      element.playbackRate = clamped
      element.preservesPitch = true
    }
    this.tempoRatios[deck] = clamped
    this.changed()
  }

  isStretchDeck(deck: DeckId): boolean {
    return this.decks?.[deck].sourceKind === "signalsmith"
  }

  async unload(deck: DeckId): Promise<void> {
    await this.decks?.[deck].release()
    this.detachExternal(deck)
    this.roles = reduceDeckRoles(this.roles, { type: "retire", deck })
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
        () => this.changed(),
      ),
      B: new DeckRuntime(
        "B", this.graph,
        () => new StretchDeckSource(this.context!, this.stretchDependencies),
        () => this.changed(),
      ),
    }
  }

  private detachExternal(deck: DeckId): void {
    this.detachExternalRoute(deck)
    this.identities[deck] = { trackId: null, queueItemId: null }
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
    const notify = () => this.changed()
    const events = [
      "play", "playing", "pause", "waiting", "ended", "error", "loadedmetadata", "seeked", "ratechange",
    ] as const
    events.forEach((event) => element.addEventListener(event, notify))
    this.externalElements[deck] = element
    this.externalListenerCleanup[deck] = () => {
      events.forEach((event) => element.removeEventListener(event, notify))
    }
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
