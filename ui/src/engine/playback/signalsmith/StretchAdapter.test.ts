import { beforeEach, describe, expect, it, vi } from "vitest"
import { StretchAdapter } from "./StretchAdapter"
import type { StretchNode } from "./types"

function fakeNode(): StretchNode {
  return {
    inputTime: 12,
    configure: vi.fn().mockResolvedValue(undefined),
    latency: vi.fn().mockResolvedValue(0.12),
    addBuffers: vi.fn().mockResolvedValue(2),
    dropBuffers: vi.fn().mockResolvedValue({ start: 0, end: 0 }),
    schedule: vi.fn().mockImplementation(async (change) => change),
    start: vi.fn().mockResolvedValue({}),
    stop: vi.fn().mockResolvedValue({}),
    setUpdateInterval: vi.fn().mockResolvedValue(undefined),
    connect: vi.fn(),
    disconnect: vi.fn(),
    port: { close: vi.fn() },
  } as unknown as StretchNode
}

describe("StretchAdapter", () => {
  let node: StretchNode
  let adapter: StretchAdapter

  beforeEach(() => {
    node = fakeNode()
    adapter = new StretchAdapter({ currentTime: 10 } as BaseAudioContext, node)
  })

  it("uses reported latency plus margin when scheduling start, seek, rate and loop changes", async () => {
    await expect(adapter.initialize("default")).resolves.toBe(0.12)
    expect(node.configure).toHaveBeenCalledWith({ preset: "default" })
    expect(adapter.schedulingLeadSeconds).toBeCloseTo(0.14)

    await adapter.start(5)
    expect(node.schedule).toHaveBeenLastCalledWith(expect.objectContaining({
      active: true,
      input: 5,
      output: 10.14,
      rate: 1,
    }))

    await adapter.seek(20, 11)
    expect(node.schedule).toHaveBeenLastCalledWith(expect.objectContaining({ input: 20, output: 11 }))

    await adapter.setRate(1.08, 12)
    expect(node.schedule).toHaveBeenLastCalledWith({ output: 12, rate: 1.08 }, true)

    await adapter.setLoop({ enabled: true, startSeconds: 16, endSeconds: 24 }, 13)
    expect(node.schedule).toHaveBeenLastCalledWith(expect.objectContaining({
      loopStart: 16,
      loopEnd: 24,
      output: 13,
      rate: 1.08,
    }))
    expect(adapter.getClockAnchor()).toEqual({ mediaSeconds: 12, audioTime: 10, rate: 1.08 })

    await adapter.stop(14)
    expect(node.stop).toHaveBeenLastCalledWith(14)
    expect(adapter.getClockAnchor().rate).toBe(0)
  })

  it("transfers appended chunks and drops old or all buffered audio", async () => {
    const left = new Float32Array(96_000)
    const right = new Float32Array(96_000)

    await expect(adapter.append([left, right])).resolves.toEqual({ start: 0, end: 2 })
    expect(node.addBuffers).toHaveBeenCalledWith([left, right], [left.buffer, right.buffer])

    vi.mocked(node.dropBuffers).mockResolvedValueOnce({ start: 1, end: 2 })
    await expect(adapter.dropBefore(1)).resolves.toEqual({ start: 1, end: 2 })
    expect(node.dropBuffers).toHaveBeenLastCalledWith(1)

    vi.mocked(node.dropBuffers).mockResolvedValueOnce({ start: 0, end: 0 })
    await expect(adapter.dropAll()).resolves.toEqual({ start: 0, end: 0 })
    expect(node.dropBuffers).toHaveBeenLastCalledWith()
  })

  it("rejects mismatched chunks before they reach the worklet", async () => {
    await expect(adapter.append([new Float32Array(4), new Float32Array(3)]))
      .rejects.toThrow("equal lengths")
    expect(node.addBuffers).not.toHaveBeenCalled()
  })

  it("rejects shared backing that cannot be placed in a transfer list", async () => {
    const shared = new Float32Array(new SharedArrayBuffer(16))

    await expect(adapter.append([shared])).rejects.toThrow("transferable ArrayBuffer")
    expect(node.addBuffers).not.toHaveBeenCalled()
  })

  it("stops, drops buffers, disconnects and closes the port exactly once", async () => {
    await adapter.release()
    await adapter.release()

    expect(node.stop).toHaveBeenCalledOnce()
    expect(node.stop).toHaveBeenCalledWith(10)
    expect(node.dropBuffers).toHaveBeenCalledOnce()
    expect(node.disconnect).toHaveBeenCalledOnce()
    expect(node.port?.close).toHaveBeenCalledOnce()
    await expect(adapter.start()).rejects.toThrow("released")
  })
})
