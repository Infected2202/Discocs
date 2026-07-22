import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { StretchDeckSource } from "./StretchDeckSource"
import { createFakeAudioContext, createFakeStretchNode, FakeAudioNode } from "../testing/webAudioFakes"

describe("StretchDeckSource full-track lifecycle", () => {
  beforeEach(() => {
    vi.stubGlobal("AudioContext", class AudioContext {})
    vi.stubGlobal("AudioWorkletNode", class AudioWorkletNode {})
    vi.stubGlobal("fetch", vi.fn())
  })

  afterEach(() => vi.unstubAllGlobals())

  it("decodes one complete buffered Blob and transfers every channel to the worklet", async () => {
    const context = createFakeAudioContext({ currentTime: 10 })
    const output = new FakeAudioNode()
    vi.mocked(context.createGain).mockReturnValue(output)
    const decoded = {
      duration: 2,
      length: 4,
      numberOfChannels: 2,
      getChannelData: vi.fn((channel: number) => new Float32Array(channel === 0 ? [1, 2, 3, 4] : [5, 6, 7, 8])),
    } as unknown as AudioBuffer
    vi.mocked(context.decodeAudioData).mockResolvedValue(decoded)
    const node = createFakeStretchNode(context)
    const createNode = vi.fn().mockResolvedValue(node)
    const source = new StretchDeckSource(context as unknown as AudioContext, { createNode })
    const blob = new Blob([new Uint8Array([1, 2, 3])])

    await expect(source.load(
      { url: "blob:deck", trackId: 7, blob },
      new AbortController().signal,
    )).resolves.toEqual({ duration: 2, objectUrl: false })

    expect(fetch).not.toHaveBeenCalled()
    expect(context.decodeAudioData).toHaveBeenCalledOnce()
    expect(createNode).toHaveBeenCalledWith(context, 2)
    const [channels, transfer] = vi.mocked(node.addBuffers).mock.calls[0]!
    expect(Array.from(channels[0])).toEqual([1, 2, 3, 4])
    expect(Array.from(channels[1])).toEqual([5, 6, 7, 8])
    expect(transfer).toEqual([channels[0].buffer, channels[1].buffer])
    expect(node.connect).toHaveBeenCalledWith(output)
    expect(source.getBufferedRanges()).toEqual([{ start: 0, end: 2 }])
  })

  it("schedules pitch-preserving transport and releases the complete worklet buffer once", async () => {
    const context = createFakeAudioContext({ currentTime: 10 })
    vi.mocked(context.createGain).mockReturnValue(new FakeAudioNode())
    vi.mocked(context.decodeAudioData).mockResolvedValue({
      duration: 2,
      length: 2,
      numberOfChannels: 1,
      getChannelData: () => new Float32Array([0.25, -0.25]),
    } as unknown as AudioBuffer)
    const node = createFakeStretchNode(context)
    const update = vi.fn()
    vi.mocked(node.setUpdateInterval).mockImplementation(async (_seconds, callback) => { update.mockImplementation(callback!) })
    const source = new StretchDeckSource(context as unknown as AudioContext, { createNode: vi.fn().mockResolvedValue(node) })
    await source.load({ url: "blob:deck", trackId: 7, blob: new Blob(["audio"]) }, new AbortController().signal)

    await source.play()
    await source.setRate(1.08)
    await source.seek(1.5)
    await source.setLoop({ enabled: true, startSeconds: 0.5, endSeconds: 1.5 })
    await source.release()
    await source.release()

    expect(node.schedule).toHaveBeenCalledWith(expect.objectContaining({ active: true, input: 0 }))
    expect(node.schedule).toHaveBeenCalledWith(expect.objectContaining({ rate: 1.08 }), true)
    expect(node.schedule).toHaveBeenCalledWith(expect.objectContaining({ input: 1.5 }))
    expect(node.schedule).toHaveBeenCalledWith(expect.objectContaining({ loopStart: 0.5, loopEnd: 1.5 }))
    expect(node.dropBuffers).toHaveBeenCalledOnce()
    expect(node.disconnect).toHaveBeenCalledOnce()
    expect(node.port?.close).toHaveBeenCalledOnce()
  })

  it("aborts a stalled worklet RPC sequence via the AbortSignal and disposes the node once it later settles", async () => {
    const context = createFakeAudioContext({ currentTime: 10 })
    vi.mocked(context.createGain).mockReturnValue(new FakeAudioNode())
    vi.mocked(context.decodeAudioData).mockResolvedValue({
      duration: 2,
      length: 2,
      numberOfChannels: 1,
      getChannelData: () => new Float32Array([0.25, -0.25]),
    } as unknown as AudioBuffer)
    const node = createFakeStretchNode(context)
    // Simulates a worklet that never replies to `configure` -- exactly the
    // hang root cause E describes, before abortRace existed to unblock it.
    let releaseConfigure!: () => void
    vi.mocked(node.configure).mockImplementation(
      () => new Promise((resolve) => { releaseConfigure = () => resolve(undefined) }),
    )
    const source = new StretchDeckSource(context as unknown as AudioContext, {
      createNode: vi.fn().mockResolvedValue(node),
    })
    const controller = new AbortController()

    const loadPromise = source.load({ url: "blob:deck", trackId: 7, blob: new Blob(["audio"]) }, controller.signal)
    await vi.waitFor(() => expect(node.configure).toHaveBeenCalled())

    controller.abort()

    await expect(loadPromise).rejects.toMatchObject({ name: "AbortError" })
    // The RPC sequence hasn't actually settled yet (configure is still
    // stalled) -- disposal must not fire before then.
    expect(node.disconnect).not.toHaveBeenCalled()
    expect(node.port?.close).not.toHaveBeenCalled()

    releaseConfigure()
    await vi.waitFor(() => {
      expect(node.disconnect).toHaveBeenCalledOnce()
      expect(node.port?.close).toHaveBeenCalledOnce()
    })
  })
})
