import type { LoopState, PlayheadAnchor } from "../types"
import type {
  StretchBufferExtent,
  StretchNode,
  StretchPreset,
  StretchSchedule,
} from "./types"

const SCHEDULING_MARGIN_SECONDS = 0.02

export class StretchAdapter {
  readonly output: AudioNode
  private readonly context: BaseAudioContext
  private node: StretchNode | null
  private latencySeconds = 0
  private rate = 1
  private playing = false
  private loop: LoopState = { enabled: false, startSeconds: 0, endSeconds: 0 }
  private extent: StretchBufferExtent = { start: 0, end: 0 }
  private released = false

  constructor(context: BaseAudioContext, node: StretchNode) {
    this.context = context
    this.node = node
    this.output = node
  }

  async initialize(preset: StretchPreset = "default"): Promise<number> {
    const node = this.requireNode()
    await node.configure({ preset })
    this.latencySeconds = await node.latency()
    return this.latencySeconds
  }

  get schedulingLeadSeconds(): number {
    return this.latencySeconds + SCHEDULING_MARGIN_SECONDS
  }

  async append(buffers: readonly Float32Array[]): Promise<StretchBufferExtent> {
    const node = this.requireNode()
    if (buffers.length === 0) throw new Error("Stretch buffers require at least one channel")
    const sampleCount = buffers[0].length
    if (buffers.some((buffer) => buffer.length !== sampleCount)) {
      throw new Error("Stretch channel buffers must have equal lengths")
    }
    const transfer = buffers.map((buffer) => {
      if (!(buffer.buffer instanceof ArrayBuffer)) {
        throw new TypeError("Stretch buffers require transferable ArrayBuffer backing")
      }
      return buffer.buffer
    })
    const end = await node.addBuffers(buffers, transfer)
    this.extent = { start: this.extent.start, end }
    return this.extent
  }

  async dropBefore(seconds: number): Promise<StretchBufferExtent> {
    this.extent = await this.requireNode().dropBuffers(seconds)
    return this.extent
  }

  async dropAll(): Promise<StretchBufferExtent> {
    this.extent = await this.requireNode().dropBuffers()
    return this.extent
  }

  async start(offsetSeconds = this.requireNode().inputTime, when?: number): Promise<void> {
    await this.schedule({ active: true, input: offsetSeconds }, when)
    this.playing = true
  }

  async stop(when?: number): Promise<void> {
    await this.requireNode().stop(this.outputTime(when))
    this.playing = false
  }

  async seek(seconds: number, when?: number): Promise<void> {
    await this.schedule({ active: this.playing, input: Math.max(0, seconds) }, when)
  }

  async setRate(rate: number, when?: number): Promise<void> {
    if (!Number.isFinite(rate) || rate <= 0) throw new RangeError("Stretch rate must be positive")
    await this.requireNode().schedule({ output: this.outputTime(when), rate }, true)
    this.rate = rate
  }

  async setLoop(loop: LoopState, when?: number): Promise<void> {
    this.loop = loop
    await this.schedule({}, when)
  }

  getClockAnchor(): PlayheadAnchor {
    return {
      mediaSeconds: this.requireNode().inputTime,
      audioTime: this.context.currentTime,
      rate: this.playing ? this.rate : 0,
    }
  }

  getBufferedExtent(): StretchBufferExtent {
    return this.extent
  }

  async release(): Promise<void> {
    if (this.released) return
    this.released = true
    const node = this.node
    this.node = null
    if (!node) return
    try {
      await node.stop(this.context.currentTime)
    } finally {
      try {
        await node.dropBuffers()
      } finally {
        node.disconnect()
        node.port?.close()
      }
    }
  }

  private async schedule(change: StretchSchedule, when?: number): Promise<void> {
    await this.requireNode().schedule({
      active: this.playing,
      rate: this.rate,
      loopStart: this.loop.enabled ? this.loop.startSeconds : 0,
      loopEnd: this.loop.enabled ? this.loop.endSeconds : 0,
      ...change,
      output: this.outputTime(when),
    })
  }

  private outputTime(when?: number): number {
    return Math.max(when ?? 0, this.context.currentTime + this.schedulingLeadSeconds)
  }

  private requireNode(): StretchNode {
    if (!this.node) throw new Error("Stretch adapter is released")
    return this.node
  }
}
