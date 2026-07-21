import type { MixerGraph } from "./MixerGraph"
import type { DeckSource, DeckSourceFactory } from "./sources/DeckSource"
import type {
  BufferedRange,
  DeckId,
  LoopState,
  PlayheadAnchor,
  SourceMetadata,
  TrackSource,
  TransportState,
} from "./types"

export interface DeckLoadOptions {
  readonly startAtSeconds?: number
  readonly autoplay?: boolean
}

export class DeckRuntime {
  readonly id: DeckId
  private readonly graph: MixerGraph
  private readonly createSource: DeckSourceFactory
  private generation = 0
  private loadController: AbortController | null = null
  private candidate: DeckSource | null = null
  private active: DeckSource | null = null
  private transport: TransportState = "idle"
  private duration: number | null = null
  private readonly notify: () => void

  constructor(
    id: DeckId,
    graph: MixerGraph,
    createSource: DeckSourceFactory,
    notify: () => void = () => undefined,
  ) {
    this.id = id
    this.graph = graph
    this.createSource = createSource
    this.notify = notify
  }

  async load(source: TrackSource, options: DeckLoadOptions = {}): Promise<SourceMetadata> {
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
    this.transport = "loading"
    this.notify()
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
      if (options.startAtSeconds !== undefined) await candidate.seek(options.startAtSeconds)
      if (options.autoplay) await candidate.play()
      const activationDelay = options.autoplay ? candidate.activationDelaySeconds ?? 0 : 0
      if (activationDelay > 0) await this.waitForActivation(activationDelay, controller.signal)
      if (!this.graph.attachSource(this.id, candidate.output, generation)) {
        await candidate.release()
        throw new DOMException("Deck graph rejected stale source generation", "AbortError")
      }
      this.candidate = null
      this.active = candidate
      candidate.setStateListener?.(() => this.notify())
      this.transport = candidate.getTransportState?.() ?? (options.autoplay ? "playing" : "paused")
      this.duration = metadata.duration
      if (this.loadController === controller) this.loadController = null
      this.notify()
      return metadata
    } catch (error) {
      if (this.candidate === candidate) this.candidate = null
      if (this.loadController === controller) this.loadController = null
      await candidate.release()
      this.transport = this.active?.getTransportState?.() ?? (this.active ? this.transport : "idle")
      this.notify()
      throw error
    }
  }

  advanceGenerationFloor(minimum: number): void {
    this.generation = Math.max(this.generation, minimum)
  }

  get sourceIdentity(): AudioNode | null {
    return this.active?.output ?? null
  }

  get sourceKind(): DeckSource["kind"] | null {
    return this.active?.kind ?? null
  }

  get currentTransport(): TransportState {
    return this.active?.getTransportState?.() ?? this.transport
  }

  get anchor(): PlayheadAnchor | null {
    return this.active?.getClockAnchor() ?? null
  }

  get buffered(): BufferedRange[] {
    return this.active?.getBufferedRanges() ?? []
  }

  get sourceDuration(): number | null {
    return this.duration
  }

  get schedulingLeadSeconds(): number {
    return this.active?.activationDelaySeconds ?? 0
  }

  async play(when?: number, offsetSeconds?: number): Promise<void> {
    const active = this.requireActive()
    await active.play(when, offsetSeconds)
    this.transport = "playing"
    this.notify()
  }

  async pause(when?: number): Promise<void> {
    const active = this.requireActive()
    await active.pause(when)
    this.transport = "paused"
    this.notify()
  }

  async seek(seconds: number, when?: number): Promise<void> {
    await this.requireActive().seek(seconds, when)
    this.notify()
  }

  async setRate(ratio: number, when?: number): Promise<void> {
    await this.requireActive().setRate(ratio, when)
    this.notify()
  }

  async setLoop(loop: LoopState, when?: number): Promise<void> {
    await this.requireActive().setLoop(loop, when)
    this.notify()
  }

  async release(): Promise<void> {
    ++this.generation
    this.loadController?.abort()
    this.loadController = null
    const candidate = this.candidate
    this.candidate = null
    if (candidate) await candidate.release()
    await this.releaseActive()
    this.transport = "idle"
    this.duration = null
    this.notify()
  }

  private async releaseActive(): Promise<void> {
    const active = this.active
    this.active = null
    if (!active) return
    active.setStateListener?.(null)
    this.graph.detachSource(this.id, active.output)
    await active.release()
  }

  private requireActive(): DeckSource {
    if (!this.active) throw new Error(`Deck ${this.id} has no loaded source`)
    return this.active
  }

  private waitForActivation(seconds: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      const onAbort = () => {
        globalThis.clearTimeout(timer)
        reject(new DOMException("Stale deck source generation", "AbortError"))
      }
      const timer = globalThis.setTimeout(() => {
        signal.removeEventListener("abort", onAbort)
        resolve()
      }, seconds * 1_000)
      signal.addEventListener("abort", onAbort, { once: true })
    })
  }
}
