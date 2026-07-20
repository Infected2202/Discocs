import { describe, expect, it, vi } from "vitest"
import { MixerGraph, type MixerGraphEvent } from "./MixerGraph"

class FakeParam {
  value = 1
  cancelScheduledValues = vi.fn()
  setValueAtTime = vi.fn((value: number) => {
    this.value = value
  })
  linearRampToValueAtTime = vi.fn((value: number) => {
    this.value = value
  })
}

class FakeNode {
  connections: FakeNode[] = []
  connect = vi.fn((destination: FakeNode) => {
    this.connections.push(destination)
    return destination
  })
  disconnect = vi.fn((destination?: FakeNode) => {
    this.connections = destination
      ? this.connections.filter((candidate) => candidate !== destination)
      : []
  })
}

class FakeGain extends FakeNode {
  gain = new FakeParam()
}

function makeContext() {
  const destination = new FakeNode()
  return {
    currentTime: 4,
    state: "running" as AudioContextState,
    destination,
    createGain: vi.fn(() => new FakeGain()),
    createDynamicsCompressor: vi.fn(() => new FakeNode()),
  } as unknown as AudioContext
}

describe("MixerGraph", () => {
  it("uses one context and schedules clamped equal-power ramps", () => {
    const events: MixerGraphEvent[] = []
    const context = makeContext()
    const graph = new MixerGraph(context, (event) => events.push(event))
    events.length = 0

    graph.setCrossfader(3, 6)

    const ramps = events.filter((event) => event.type === "parameter-ramp")
    expect(ramps.map((event) => event.value)).toEqual([0, 1])
    expect(ramps.every((event) => event.startTime === 6 && event.endTime === 6.015)).toBe(true)
    expect(ramps.every((event) => event.contextState === "running")).toBe(true)
  })

  it("does not disconnect Deck A while Deck B is attached", () => {
    const graph = new MixerGraph(makeContext())
    const sourceA = new FakeNode()
    const sourceB = new FakeNode()

    expect(graph.attachSource("A", sourceA as unknown as AudioNode, 1)).toBe(true)
    expect(graph.attachSource("B", sourceB as unknown as AudioNode, 1)).toBe(true)

    expect(graph.getAttachedSource("A")).toBe(sourceA)
    expect(sourceA.disconnect).not.toHaveBeenCalled()
    expect(graph.getAttachedSource("B")).toBe(sourceB)
  })

  it("rejects an older source generation", () => {
    const graph = new MixerGraph(makeContext())
    const current = new FakeNode()
    const stale = new FakeNode()
    graph.attachSource("A", current as unknown as AudioNode, 2)

    expect(graph.attachSource("A", stale as unknown as AudioNode, 1)).toBe(false)
    expect(graph.getAttachedSource("A")).toBe(current)
    expect(stale.connect).not.toHaveBeenCalled()
  })
})
