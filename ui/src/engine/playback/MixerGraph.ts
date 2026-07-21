import {
  channelFaderGain,
  clampNormalized,
  eqGainDb,
  djCrossfaderGains,
  filterFrequencies,
  parameterRampWindow,
  trimGain,
} from "./curves"
import type { DeckId, EqBand } from "./types"

export type MixerGraphEvent =
  | {
      type: "context-state"
      state: AudioContextState
      audioTime: number
    }
  | {
      type: "parameter-ramp"
      deck?: DeckId
      parameter: "crossfader" | "channel-fader" | "master-gain" | "trim" | "eq" | "filter"
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
  trim: GainNode
  eq: Record<EqBand, BiquadFilterNode>
  lowpass: BiquadFilterNode
  highpass: BiquadFilterNode
  channel: GainNode
  crossfader: GainNode
  meter: AnalyserNode
  source: AudioNode | null
  generation: number
}

export class MixerGraph {
  private readonly context: AudioContext
  private readonly log: MixerGraphLogger
  private readonly mixBus: GainNode
  private readonly master: GainNode
  private readonly protection: DynamicsCompressorNode
  private readonly masterMeter: AnalyserNode
  private readonly strips: Record<DeckId, DeckStrip>
  private readonly meterBuffers = new WeakMap<AnalyserNode, Float32Array<ArrayBuffer>>()
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
    this.protection.threshold.value = -1
    this.protection.knee.value = 0
    this.protection.ratio.value = 20
    this.protection.attack.value = 0.003
    this.protection.release.value = 0.1
    this.masterMeter = context.createAnalyser()
    this.mixBus.connect(this.master)
    this.master.connect(this.protection)
    this.protection.connect(context.destination)
    this.protection.connect(this.masterMeter)
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
    const gains = djCrossfaderGains(value)
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

  setTrim(deck: DeckId, value: number, when?: number): void {
    this.schedule(this.strips[deck].trim.gain, trimGain(value), "trim", deck, when)
  }

  setEq(deck: DeckId, band: EqBand, value: number, when?: number): void {
    this.schedule(this.strips[deck].eq[band].gain, eqGainDb(band, value), "eq", deck, when)
  }

  setFilter(deck: DeckId, value: number, when?: number): void {
    const frequencies = filterFrequencies(value)
    this.schedule(this.strips[deck].lowpass.frequency, frequencies.lowpassHz, "filter", deck, when)
    this.schedule(this.strips[deck].highpass.frequency, frequencies.highpassHz, "filter", deck, when)
  }

  setMasterGain(value: number, when?: number): void {
    this.schedule(this.master.gain, clampNormalized(value), "master-gain", undefined, when)
  }

  getAttachedSource(deck: DeckId): AudioNode | null {
    return this.strips[deck].source
  }

  readMeters(): Record<DeckId | "master", number> {
    return {
      A: this.readMeter(this.strips.A.meter),
      B: this.readMeter(this.strips.B.meter),
      master: this.readMeter(this.masterMeter),
    }
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
      strip.trim.disconnect()
      Object.values(strip.eq).forEach((node) => node.disconnect())
      strip.lowpass.disconnect()
      strip.highpass.disconnect()
      strip.channel.disconnect()
      strip.crossfader.disconnect()
      strip.meter.disconnect()
      strip.source = null
    }
    this.mixBus.disconnect()
    this.master.disconnect()
    this.protection.disconnect()
    this.masterMeter.disconnect()
  }

  private createStrip(): DeckStrip {
    const input = this.context.createGain()
    const trim = this.context.createGain()
    const low = this.context.createBiquadFilter()
    low.type = "lowshelf"
    low.frequency.value = 250
    const mid = this.context.createBiquadFilter()
    mid.type = "peaking"
    mid.frequency.value = 1_000
    mid.Q.value = 0.8
    const high = this.context.createBiquadFilter()
    high.type = "highshelf"
    high.frequency.value = 4_000
    const lowpass = this.context.createBiquadFilter()
    lowpass.type = "lowpass"
    lowpass.frequency.value = 20_000
    const highpass = this.context.createBiquadFilter()
    highpass.type = "highpass"
    highpass.frequency.value = 20
    const channel = this.context.createGain()
    const crossfader = this.context.createGain()
    const meter = this.context.createAnalyser()
    input.connect(trim)
    trim.connect(low)
    low.connect(mid)
    mid.connect(high)
    high.connect(lowpass)
    lowpass.connect(highpass)
    highpass.connect(channel)
    channel.connect(crossfader)
    crossfader.connect(this.mixBus)
    channel.connect(meter)
    trim.gain.value = 1
    channel.gain.value = 1
    return {
      input, trim, eq: { low, mid, high }, lowpass, highpass,
      channel, crossfader, meter, source: null, generation: 0,
    }
  }

  private schedule(
    parameter: AudioParam,
    value: number,
    name: "crossfader" | "channel-fader" | "master-gain" | "trim" | "eq" | "filter",
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

  private readMeter(analyser: AnalyserNode): number {
    let samples = this.meterBuffers.get(analyser)
    if (!samples || samples.length !== analyser.fftSize) {
      samples = new Float32Array(analyser.fftSize)
      this.meterBuffers.set(analyser, samples)
    }
    analyser.getFloatTimeDomainData(samples)
    let peak = 0
    for (const sample of samples) peak = Math.max(peak, Math.abs(sample))
    return peak
  }
}
