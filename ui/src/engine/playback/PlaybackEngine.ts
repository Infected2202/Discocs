import { DeckRuntime } from "./DeckRuntime"
import { MixerGraph, type MixerGraphLogger } from "./MixerGraph"
import { detectPlaybackCapabilities } from "./capabilities"
import { HtmlMediaDeckSource } from "./sources/HtmlMediaDeckSource"
import type { DeckId, PlaybackCapabilities, SourceMetadata, TrackSource } from "./types"

export class PlaybackEngine {
  private readonly log: MixerGraphLogger
  private context: AudioContext | null = null
  private graph: MixerGraph | null = null
  private decks: Record<DeckId, DeckRuntime> | null = null
  private programNode: MediaElementAudioSourceNode | null = null
  private programGeneration = 0

  constructor(log: MixerGraphLogger = () => undefined) {
    this.log = log
  }

  async ensureReady(): Promise<PlaybackCapabilities> {
    const capabilities = detectPlaybackCapabilities()
    if (!capabilities.manualMix) return capabilities
    this.initialize()
    const context = this.context
    if (!context) return capabilities
    if (context.state === "suspended") await context.resume()
    this.graph?.logContextState()
    return capabilities
  }

  routeProgramElement(element: HTMLMediaElement): boolean {
    const capabilities = detectPlaybackCapabilities()
    if (!capabilities.manualMix) return false
    this.initialize()
    const context = this.context
    const graph = this.graph
    if (!context || !graph) return false

    const node = context.createMediaElementSource(element)
    const previous = this.programNode
    if (previous) graph.detachSource("A", previous)
    this.programNode = node
    this.programGeneration += 1
    if (!graph.attachSource("A", node, this.programGeneration)) {
      node.disconnect()
      this.programNode = previous
      if (previous) graph.attachSource("A", previous, this.programGeneration)
      return false
    }
    previous?.disconnect()
    return true
  }

  async load(deck: DeckId, source: TrackSource): Promise<SourceMetadata> {
    const capabilities = await this.ensureReady()
    if (!capabilities.manualMix || !this.decks) {
      throw new Error(capabilities.reasons.join("; ") || "Web Audio mixer is unavailable")
    }
    if (deck === "A") this.decks.A.advanceGenerationFloor(this.programGeneration)
    return this.decks[deck].load(source)
  }

  async unload(deck: DeckId): Promise<void> {
    await this.decks?.[deck].release()
  }

  setCrossfader(value: number, when?: number): void {
    if (!this.graph) throw new Error("Playback engine is not initialized")
    this.graph.setCrossfader(value, when)
  }

  async destroy(): Promise<void> {
    const decks = this.decks
    this.decks = null
    if (decks) await Promise.all([decks.A.release(), decks.B.release()])
    const programNode = this.programNode
    this.programNode = null
    if (programNode) {
      this.graph?.detachSource("A", programNode)
      programNode.disconnect()
    }
    this.graph?.destroy()
    this.graph = null
    const context = this.context
    this.context = null
    if (context && context.state !== "closed") await context.close()
  }

  private initialize(): void {
    if (this.context) return
    this.context = new AudioContext()
    this.graph = new MixerGraph(this.context, this.log)
    // Phase 1 has only Deck A. Put it at unity instead of the -3 dB centre of
    // an equal-power crossfader; Deck B remains silent until Phase 2.
    this.graph.setCrossfader(-1)
    this.graph.setChannelFader("B", 0)
    this.decks = {
      A: new DeckRuntime("A", this.graph, () => new HtmlMediaDeckSource(this.context!)),
      B: new DeckRuntime("B", this.graph, () => new HtmlMediaDeckSource(this.context!)),
    }
  }
}
