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
  readonly tempoRatio?: number
}

// Real-world default: generous enough for a Signalsmith worklet cold-start
// (compile + configure + full-track buffer transfer) on a slow device, but
// still a hard ceiling -- a `load()` that never settles is exactly root
// cause E (SYNC_REWRITE_PLAN.md §2.3), and hanging forever is worse for the
// user than a loud, recoverable failure.
const DEFAULT_LOAD_TIMEOUT_MS = 15_000

export class DeckRuntime {
  readonly id: DeckId
  private readonly graph: MixerGraph
  private readonly createSource: DeckSourceFactory
  private readonly loadTimeoutMs: number
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
    loadTimeoutMs: number = DEFAULT_LOAD_TIMEOUT_MS,
  ) {
    this.id = id
    this.graph = graph
    this.createSource = createSource
    this.notify = notify
    this.loadTimeoutMs = loadTimeoutMs
  }

  async load(source: TrackSource, options: DeckLoadOptions = {}): Promise<SourceMetadata> {
    const generation = ++this.generation
    this.loadController?.abort()
    const controller = new AbortController()
    this.loadController = controller
    // One timer bounds the *entire* load(): the worklet RPC sequence inside
    // candidate.load() (root cause E), but also seek/setRate/play and the
    // post-activation attach below -- any of these stalling is just as much
    // a hang from the caller's point of view. Aborting `controller` here
    // reuses every existing generation/signal check below rather than adding
    // a parallel timeout code path.
    const timeoutReason = new DOMException(`Deck ${this.id} load timed out`, "TimeoutError")
    const timer = globalThis.setTimeout(() => controller.abort(timeoutReason), this.loadTimeoutMs)

    try {
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
        throw this.staleOrTimeoutError(controller.signal)
      }

      try {
        const metadata = await candidate.load(source, controller.signal)
        if (generation !== this.generation || controller.signal.aborted) {
          await candidate.release()
          throw this.staleOrTimeoutError(controller.signal)
        }
        if (options.startAtSeconds !== undefined) await candidate.seek(options.startAtSeconds)
        if (options.tempoRatio !== undefined) await candidate.setRate(options.tempoRatio)
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
    } finally {
      globalThis.clearTimeout(timer)
    }
  }

  private staleOrTimeoutError(signal: AbortSignal): Error {
    const reason = (signal as { reason?: unknown }).reason
    // jsdom's DOMException does not extend Error, so an explicit instanceof
    // check against Error alone misses reasons built with `new DOMException(...)`.
    if (reason instanceof Error || reason instanceof DOMException) return reason as Error
    return new DOMException("Stale deck source generation", "AbortError")
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
        reject(this.staleOrTimeoutError(signal))
      }
      const timer = globalThis.setTimeout(() => {
        signal.removeEventListener("abort", onAbort)
        resolve()
      }, seconds * 1_000)
      signal.addEventListener("abort", onAbort, { once: true })
    })
  }
}
