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

  constructor(log: MixerGraphLogger = () => undefined) {
    this.log = log
  }

  async ensureReady(): Promise<PlaybackCapabilities> {
    const capabilities = detectPlaybackCapabilities()
    if (!capabilities.manualMix) return capabilities
    if (!this.context) {
      this.context = new AudioContext()
      this.graph = new MixerGraph(this.context, this.log)
      this.decks = {
        A: new DeckRuntime("A", this.graph, () => new HtmlMediaDeckSource(this.context!)),
        B: new DeckRuntime("B", this.graph, () => new HtmlMediaDeckSource(this.context!)),
      }
    }
    if (this.context.state === "suspended") await this.context.resume()
    this.graph?.logContextState()
    return capabilities
  }

  async load(deck: DeckId, source: TrackSource): Promise<SourceMetadata> {
    const capabilities = await this.ensureReady()
    if (!capabilities.manualMix || !this.decks) {
      throw new Error(capabilities.reasons.join("; ") || "Web Audio mixer is unavailable")
    }
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
    this.graph?.destroy()
    this.graph = null
    const context = this.context
    this.context = null
    if (context && context.state !== "closed") await context.close()
  }
}
