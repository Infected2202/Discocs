import {
  channelFaderGain,
  clampNormalized,
  equalPowerCrossfader,
  parameterRampWindow,
} from "./curves"
import type { DeckId } from "./types"

export type MixerGraphEvent =
  | {
      type: "context-state"
      state: AudioContextState
      audioTime: number
    }
  | {
      type: "parameter-ramp"
      deck?: DeckId
      parameter: "crossfader" | "channel-fader" | "master-gain"
      value: number
      startTime: number
      endTime: number
      contextState: AudioContextState
    }
  | {
      type: "source-attachment"
      deck: DeckId
      generation: number
      audioTime: number
    }

export type MixerGraphLogger = (event: MixerGraphEvent) => void

interface DeckStrip {
  input: GainNode
  channel: GainNode
  crossfader: GainNode
  source: AudioNode | null
  generation: number
}

export class MixerGraph {
  private readonly context: AudioContext
  private readonly log: MixerGraphLogger
  private readonly mixBus: GainNode
  private readonly master: GainNode
  private readonly protection: DynamicsCompressorNode
  private readonly strips: Record<DeckId, DeckStrip>
  private readonly handleContextStateChange = () => this.logContextState()
  private destroyed = false

  constructor(
    context: AudioContext,
    log: MixerGraphLogger = () => undefined,
  ) {
    this.context = context
    this.log = log
    this.mixBus = context.createGain()
    this.master = context.createGain()
    this.protection = context.createDynamicsCompressor()
    this.mixBus.connect(this.master)
    this.master.connect(this.protection)
    this.protection.connect(context.destination)
    this.strips = {
      A: this.createStrip(),
      B: this.createStrip(),
    }
    this.context.addEventListener?.("statechange", this.handleContextStateChange)
    this.setCrossfader(0)
    this.logContextState()
  }

  attachSource(deck: DeckId, source: AudioNode, generation: number): boolean {
    if (this.destroyed) return false
    const strip = this.strips[deck]
    if (generation < strip.generation) return false
    if (strip.source !== source) {
      strip.source?.disconnect(strip.input)
      source.connect(strip.input)
      strip.source = source
    }
    strip.generation = generation
    this.log({ type: "source-attachment", deck, generation, audioTime: this.context.currentTime })
    return true
  }

  detachSource(deck: DeckId, source: AudioNode): void {
    const strip = this.strips[deck]
    if (strip.source !== source) return
    source.disconnect(strip.input)
    strip.source = null
  }

  setCrossfader(value: number, when?: number): void {
    const gains = equalPowerCrossfader(value)
    this.schedule(this.strips.A.crossfader.gain, gains.A, "crossfader", "A", when)
    this.schedule(this.strips.B.crossfader.gain, gains.B, "crossfader", "B", when)
  }

  setChannelFader(deck: DeckId, value: number, when?: number): void {
    this.schedule(
      this.strips[deck].channel.gain,
      channelFaderGain(value),
      "channel-fader",
      deck,
      when,
    )
  }

  setMasterGain(value: number, when?: number): void {
    this.schedule(this.master.gain, clampNormalized(value), "master-gain", undefined, when)
  }

  getAttachedSource(deck: DeckId): AudioNode | null {
    return this.strips[deck].source
  }

  logContextState(): void {
    this.log({
      type: "context-state",
      state: this.context.state,
      audioTime: this.context.currentTime,
    })
  }

  destroy(): void {
    if (this.destroyed) return
    this.destroyed = true
    this.context.removeEventListener?.("statechange", this.handleContextStateChange)
    for (const deck of ["A", "B"] as const) {
      const strip = this.strips[deck]
      if (strip.source) strip.source.disconnect(strip.input)
      strip.input.disconnect()
      strip.channel.disconnect()
      strip.crossfader.disconnect()
      strip.source = null
    }
    this.mixBus.disconnect()
    this.master.disconnect()
    this.protection.disconnect()
  }

  private createStrip(): DeckStrip {
    const input = this.context.createGain()
    const channel = this.context.createGain()
    const crossfader = this.context.createGain()
    input.connect(channel)
    channel.connect(crossfader)
    crossfader.connect(this.mixBus)
    channel.gain.value = 1
    return { input, channel, crossfader, source: null, generation: 0 }
  }

  private schedule(
    parameter: AudioParam,
    value: number,
    name: "crossfader" | "channel-fader" | "master-gain",
    deck?: DeckId,
    when?: number,
  ): void {
    const window = parameterRampWindow(this.context.currentTime, when)
    parameter.cancelScheduledValues(window.startTime)
    parameter.setValueAtTime(parameter.value, window.startTime)
    parameter.linearRampToValueAtTime(value, window.endTime)
    this.log({
      type: "parameter-ramp",
      deck,
      parameter: name,
      value,
      ...window,
      contextState: this.context.state,
    })
  }
}
