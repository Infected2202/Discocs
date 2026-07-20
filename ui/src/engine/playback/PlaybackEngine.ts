import { DeckRuntime } from "./DeckRuntime"
import { freeDeck, initialDeckRoleState, reduceDeckRoles, type DeckRoleState } from "./deckRoles"
import { MixerGraph, type MixerGraphLogger } from "./MixerGraph"
import { detectPlaybackCapabilities } from "./capabilities"
import { HtmlMediaDeckSource } from "./sources/HtmlMediaDeckSource"
import { clampBipolar, clampNormalized } from "./curves"
import type {
  DeckId,
  DeckSnapshot,
  EqBand,
  HandoverRequest,
  HandoverResult,
  PlaybackCapabilities,
  PlaybackEngineSnapshot,
  SourceMetadata,
  TrackSource,
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
})

export class PlaybackEngine {
  private readonly log: MixerGraphLogger
  private context: AudioContext | null = null
  private graph: MixerGraph | null = null
  private decks: Record<DeckId, DeckRuntime> | null = null
  private externalNodes: Record<DeckId, MediaElementAudioSourceNode | null> = { A: null, B: null }
  private readonly generations: Record<DeckId, number> = { A: 0, B: 0 }
  private identities: Record<DeckId, { trackId: number | null; queueItemId: string | null }> = {
    A: { trackId: null, queueItemId: null },
    B: { trackId: null, queueItemId: null },
  }
  private roles: DeckRoleState = initialDeckRoleState()
  private revision = 0
  private readonly listeners = new Set<() => void>()
  private readonly mixer = {
    crossfader: -1,
    masterGain: 1,
    channelFaders: { A: 1, B: 1 },
    trims: { A: 0.5, B: 0.5 },
    eq: {
      A: { low: 0.8, mid: 0.8, high: 0.8 },
      B: { low: 0.8, mid: 0.8, high: 0.8 },
    },
    filters: { A: 0, B: 0 },
  }

  constructor(log: MixerGraphLogger = () => undefined) {
    this.log = log
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
    const capabilities = detectPlaybackCapabilities()
    if (!capabilities.manualMix) return false
    this.initialize()
    const context = this.context
    const graph = this.graph
    if (!context || !graph) return false

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
    this.identities[deck] = { trackId, queueItemId }
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
    const decks: Record<DeckId, DeckSnapshot> = { A: emptyDeck("A"), B: emptyDeck("B") }
    for (const deck of ["A", "B"] as const) {
      decks[deck].role = this.roles.roles[deck]
      decks[deck].preparation = this.roles.preparation[deck]
      decks[deck].trackId = this.identities[deck].trackId
      decks[deck].queueItemId = this.identities[deck].queueItemId
    }
    return {
      revision: this.revision,
      contextState: this.context?.state ?? "uninitialized",
      programDeck: this.roles.program,
      decks,
      mixer: { ...this.mixer, meters: this.graph?.readMeters() ?? { A: 0, B: 0, master: 0 } },
      automation: { owner: "none" },
      capabilities,
      error: null,
    }
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
    this.changed()
  }

  private initialize(): void {
    if (this.context) return
    this.context = new AudioContext()
    this.graph = new MixerGraph(this.context, this.log)
    this.graph.setCrossfader(-1)
    this.decks = {
      A: new DeckRuntime("A", this.graph, () => new HtmlMediaDeckSource(this.context!)),
      B: new DeckRuntime("B", this.graph, () => new HtmlMediaDeckSource(this.context!)),
    }
  }

  private detachExternal(deck: DeckId): void {
    const node = this.externalNodes[deck]
    if (!node) return
    this.graph?.detachSource(deck, node)
    node.disconnect()
    this.externalNodes[deck] = null
    this.identities[deck] = { trackId: null, queueItemId: null }
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
