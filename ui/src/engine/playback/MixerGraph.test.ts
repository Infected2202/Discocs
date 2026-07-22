import { describe, expect, it, vi } from "vitest"
import { MixerGraph, type MixerGraphEvent } from "./MixerGraph"
import { createFakeAudioContext, FakeAudioNode } from "./testing/webAudioFakes"

function makeContext() {
  return createFakeAudioContext({ currentTime: 4, state: "running" }) as unknown as AudioContext
}

describe("MixerGraph", () => {
  it("keeps both decks at unity in the centre and clamps edge ramps", () => {
    const events: MixerGraphEvent[] = []
    const context = makeContext()
    const graph = new MixerGraph(context, (event) => events.push(event))
    events.length = 0

    graph.setCrossfader(0)
    let ramps = events.filter((event) => event.type === "parameter-ramp")
    expect(ramps.map((event) => event.value)).toEqual([1, 1])

    events.length = 0
    graph.setCrossfader(3, 6)

    ramps = events.filter((event) => event.type === "parameter-ramp")
    expect(ramps[0]?.value).toBe(0)
    expect(ramps[1]?.value).toBe(1)
    expect(ramps.every((event) => event.startTime === 6 && event.endTime === 6.015)).toBe(true)
    expect(ramps.every((event) => event.contextState === "running")).toBe(true)
  })

  it("configures explicit peak protection instead of the compressor defaults", () => {
    const context = makeContext()
    new MixerGraph(context)
    const protection = vi.mocked(context.createDynamicsCompressor).mock.results[0]?.value as FakeAudioNode

    expect(protection.threshold.value).toBe(-1)
    expect(protection.knee.value).toBe(0)
    expect(protection.ratio.value).toBe(20)
    expect(protection.attack.value).toBe(0.003)
    expect(protection.release.value).toBe(0.1)
  })

  it("reuses analyser buffers across meter ticks", () => {
    const context = makeContext()
    const graph = new MixerGraph(context)
    graph.readMeters()
    graph.readMeters()
    const analyser = vi.mocked(context.createAnalyser).mock.results[0]?.value as FakeAudioNode

    expect(analyser.getFloatTimeDomainData).toHaveBeenCalledTimes(2)
    expect(analyser.getFloatTimeDomainData.mock.calls[0]?.[0]).toBe(
      analyser.getFloatTimeDomainData.mock.calls[1]?.[0],
    )
  })

  it("does not disconnect Deck A while Deck B is attached", () => {
    const graph = new MixerGraph(makeContext())
    const sourceA = new FakeAudioNode()
    const sourceB = new FakeAudioNode()

    expect(graph.attachSource("A", sourceA as unknown as AudioNode, 1)).toBe(true)
    expect(graph.attachSource("B", sourceB as unknown as AudioNode, 1)).toBe(true)

    expect(graph.getAttachedSource("A")).toBe(sourceA)
    expect(sourceA.disconnect).not.toHaveBeenCalled()
    expect(graph.getAttachedSource("B")).toBe(sourceB)
  })

  it("rejects an older source generation", () => {
    const graph = new MixerGraph(makeContext())
    const current = new FakeAudioNode()
    const stale = new FakeAudioNode()
    graph.attachSource("A", current as unknown as AudioNode, 2)

    expect(graph.attachSource("A", stale as unknown as AudioNode, 1)).toBe(false)
    expect(graph.getAttachedSource("A")).toBe(current)
    expect(stale.connect).not.toHaveBeenCalled()
  })
})
