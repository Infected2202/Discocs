import type { MixerGraph } from "./MixerGraph"
import type { DeckSource, DeckSourceFactory } from "./sources/DeckSource"
import type { DeckId, SourceMetadata, TrackSource } from "./types"

export class DeckRuntime {
  readonly id: DeckId
  private readonly graph: MixerGraph
  private readonly createSource: DeckSourceFactory
  private generation = 0
  private loadController: AbortController | null = null
  private candidate: DeckSource | null = null
  private active: DeckSource | null = null

  constructor(
    id: DeckId,
    graph: MixerGraph,
    createSource: DeckSourceFactory,
  ) {
    this.id = id
    this.graph = graph
    this.createSource = createSource
  }

  async load(source: TrackSource): Promise<SourceMetadata> {
    const generation = ++this.generation
    this.loadController?.abort()
    const controller = new AbortController()
    this.loadController = controller

    const supersededCandidate = this.candidate
    const supersededActive = this.active
    this.active = null
    if (supersededActive) this.graph.detachSource(this.id, supersededActive.output)
    const candidate = this.createSource()
    this.candidate = candidate
    if (supersededCandidate) await supersededCandidate.release()
    if (supersededActive) await supersededActive.release()
    if (generation !== this.generation || controller.signal.aborted) {
      await candidate.release()
      throw new DOMException("Stale deck source generation", "AbortError")
    }

    try {
      const metadata = await candidate.load(source, controller.signal)
      if (generation !== this.generation || controller.signal.aborted) {
        await candidate.release()
        throw new DOMException("Stale deck source generation", "AbortError")
      }
      if (!this.graph.attachSource(this.id, candidate.output, generation)) {
        await candidate.release()
        throw new DOMException("Deck graph rejected stale source generation", "AbortError")
      }
      this.candidate = null
      this.active = candidate
      if (this.loadController === controller) this.loadController = null
      return metadata
    } catch (error) {
      if (this.candidate === candidate) this.candidate = null
      if (this.loadController === controller) this.loadController = null
      await candidate.release()
      throw error
    }
  }

  advanceGenerationFloor(minimum: number): void {
    this.generation = Math.max(this.generation, minimum)
  }

  get sourceIdentity(): AudioNode | null {
    return this.active?.output ?? null
  }

  async release(): Promise<void> {
    ++this.generation
    this.loadController?.abort()
    this.loadController = null
    const candidate = this.candidate
    this.candidate = null
    if (candidate) await candidate.release()
    await this.releaseActive()
  }

  private async releaseActive(): Promise<void> {
    const active = this.active
    this.active = null
    if (!active) return
    this.graph.detachSource(this.id, active.output)
    await active.release()
  }
}
