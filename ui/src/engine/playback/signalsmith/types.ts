export type StretchPreset = "default" | "cheaper"

export interface StretchConfiguration {
  readonly preset?: StretchPreset
  readonly blockMs?: number
  readonly intervalMs?: number
  readonly splitComputation?: boolean
}

export interface StretchSchedule {
  readonly active?: boolean
  readonly input?: number
  readonly output?: number
  readonly rate?: number
  readonly semitones?: number
  readonly tonalityHz?: number
  readonly formantSemitones?: number
  readonly formantCompensation?: boolean
  readonly formantBaseHz?: number
  readonly loopStart?: number
  readonly loopEnd?: number
}

export interface StretchBufferExtent {
  readonly start: number
  readonly end: number
}

export interface StretchNode extends AudioNode {
  inputTime: number
  readonly port?: Pick<MessagePort, "close">
  configure(configuration: StretchConfiguration): Promise<void>
  latency(): Promise<number>
  addBuffers(buffers: readonly Float32Array[], transfer?: readonly ArrayBuffer[]): Promise<number>
  dropBuffers(toSeconds?: number): Promise<StretchBufferExtent>
  schedule(change: StretchSchedule, adjustPrevious?: boolean): Promise<StretchSchedule>
  start(when?: number, offset?: number, duration?: number, rate?: number, semitones?: number): Promise<StretchSchedule>
  stop(when?: number): Promise<StretchSchedule>
  setUpdateInterval(seconds: number, callback?: (inputTime: number) => void): Promise<void>
}
