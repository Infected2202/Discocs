import { describe, expect, it, vi } from "vitest"
import { DeckRuntime } from "./DeckRuntime"
import type { MixerGraph } from "./MixerGraph"
import type { DeckSource } from "./sources/DeckSource"
import type { SourceMetadata, TrackSource } from "./types"

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function fakeNode(): AudioNode {
  return { connect: vi.fn(), disconnect: vi.fn() } as unknown as AudioNode
}

function fakeSource(load: DeckSource["load"]): DeckSource {
  return {
    kind: "media-element",
    output: fakeNode(),
    load,
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    seek: vi.fn().mockResolvedValue(undefined),
    setRate: vi.fn(),
    setLoop: vi.fn(),
    getClockAnchor: vi.fn(),
    getBufferedRanges: vi.fn(() => []),
    release: vi.fn().mockResolvedValue(undefined),
  }
}

function fakeGraph(): MixerGraph {
  const attachments = new Map<string, AudioNode>()
  return {
    attachSource: vi.fn((deck: string, source: AudioNode) => {
      attachments.set(deck, source)
      return true
    }),
    detachSource: vi.fn((deck: string, source: AudioNode) => {
      if (attachments.get(deck) === source) attachments.delete(deck)
    }),
    getAttachedSource: vi.fn((deck: string) => attachments.get(deck) ?? null),
  } as unknown as MixerGraph
}

const metadata: SourceMetadata = { duration: 120, objectUrl: false }
const track = (trackId: number): TrackSource => ({ url: `/tracks/${trackId}`, trackId })

describe("DeckRuntime source generations", () => {
  it("never attaches a stale source completion", async () => {
    const firstLoad = deferred<SourceMetadata>()
    const firstSignals: AbortSignal[] = []
    const first = fakeSource((_source, signal) => {
      firstSignals.push(signal)
      return firstLoad.promise
    })
    const second = fakeSource(async () => metadata)
    const graph = fakeGraph()
    const sources = [first, second]
    const runtime = new DeckRuntime("A", graph, () => sources.shift()!)

    const stalePromise = runtime.load(track(1))
    const currentPromise = runtime.load(track(2))
    firstLoad.resolve(metadata)

    await expect(stalePromise).rejects.toMatchObject({ name: "AbortError" })
    await expect(currentPromise).resolves.toEqual(metadata)
    expect(firstSignals).toHaveLength(1)
    expect(firstSignals[0]?.aborted).toBe(true)
    expect(graph.attachSource).toHaveBeenCalledTimes(1)
    expect(graph.attachSource).toHaveBeenCalledWith("A", second.output, 2)
    expect(first.release).toHaveBeenCalled()
  })

  it("release is idempotent", async () => {
    const source = fakeSource(async () => metadata)
    const runtime = new DeckRuntime("A", fakeGraph(), () => source)
    await runtime.load(track(1))

    await runtime.release()
    await runtime.release()

    expect(source.release).toHaveBeenCalledTimes(1)
  })

  it("detaches and releases the previous active source on replacement", async () => {
    const first = fakeSource(async () => metadata)
    const second = fakeSource(async () => metadata)
    const graph = fakeGraph()
    const sources = [first, second]
    const runtime = new DeckRuntime("A", graph, () => sources.shift()!)
    await runtime.load(track(1))

    await runtime.load(track(2))

    expect(graph.detachSource).toHaveBeenCalledWith("A", first.output)
    expect(first.release).toHaveBeenCalledTimes(1)
    expect(runtime.sourceIdentity).toBe(second.output)
  })

  it("constructing Deck B does not change Deck A transport identity", async () => {
    const sourceA = fakeSource(async () => metadata)
    const graph = fakeGraph()
    const deckA = new DeckRuntime("A", graph, () => sourceA)
    await deckA.load(track(1))
    const identity = deckA.sourceIdentity

    new DeckRuntime("B", graph, () => fakeSource(async () => metadata))

    expect(deckA.sourceIdentity).toBe(identity)
    expect(graph.getAttachedSource("A")).toBe(identity)
    expect(sourceA.release).not.toHaveBeenCalled()
  })

  it("advances past an adopted compatibility-source generation", async () => {
    const source = fakeSource(async () => metadata)
    const graph = fakeGraph()
    const runtime = new DeckRuntime("A", graph, () => source)

    runtime.advanceGenerationFloor(7)
    await runtime.load(track(1))

    expect(graph.attachSource).toHaveBeenCalledWith("A", source.output, 8)
  })
})

describe("DeckRuntime load timeout", () => {
  // Simulates a DeckSource whose load() is abort-aware (as StretchDeckSource
  // now is via abortRace): it never settles on its own, only in reaction to
  // the signal it was given -- and it rethrows the signal's own abort reason,
  // the same way abortRace does. This is what lets a caller distinguish "the
  // overall load() timed out" from an ordinary stale-generation abort.
  function abortAwareSource(): DeckSource {
    return fakeSource((_source, signal) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        const abortReason = (signal as { reason?: unknown }).reason
        reject(abortReason instanceof Error ? abortReason : new DOMException("Aborted", "AbortError"))
      }, { once: true })
    }))
  }

  it("rejects with a distinguishable TimeoutError once the injected load timeout elapses", async () => {
    const runtime = new DeckRuntime("A", fakeGraph(), () => abortAwareSource(), undefined, 10)

    await expect(runtime.load(track(1))).rejects.toMatchObject({ name: "TimeoutError" })
  })

  it("never times out while the candidate settles well within the configured window", async () => {
    const source = fakeSource(async () => metadata)
    const runtime = new DeckRuntime("A", fakeGraph(), () => source, undefined, 10_000)

    await expect(runtime.load(track(1))).resolves.toEqual(metadata)
  })

  it("releases the hung candidate once the timeout fires", async () => {
    const source = abortAwareSource()
    const runtime = new DeckRuntime("A", fakeGraph(), () => source, undefined, 10)

    await expect(runtime.load(track(1))).rejects.toMatchObject({ name: "TimeoutError" })
    expect(source.release).toHaveBeenCalled()
  })
})
