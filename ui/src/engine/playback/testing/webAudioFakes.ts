import { vi } from "vitest"
import type { StretchBufferExtent, StretchNode, StretchSchedule } from "../signalsmith/types"

export class FakeAudioParam {
  value = 1
  cancelScheduledValues = vi.fn()
  setValueAtTime = vi.fn((value: number) => {
    this.value = value
  })
  linearRampToValueAtTime = vi.fn((value: number) => {
    this.value = value
  })
}

export class FakeAudioNode {
  connections: FakeAudioNode[] = []
  readonly gain = new FakeAudioParam()
  readonly frequency = new FakeAudioParam()
  readonly Q = new FakeAudioParam()
  readonly threshold = new FakeAudioParam()
  readonly knee = new FakeAudioParam()
  readonly ratio = new FakeAudioParam()
  readonly attack = new FakeAudioParam()
  readonly release = new FakeAudioParam()
  type = "peaking"
  fftSize = 32
  connect = vi.fn((destination?: FakeAudioNode) => {
    if (destination) this.connections.push(destination)
    return destination
  })
  disconnect = vi.fn((destination?: FakeAudioNode) => {
    this.connections = destination
      ? this.connections.filter((candidate) => candidate !== destination)
      : []
  })
  getFloatTimeDomainData = vi.fn((samples: Float32Array) => samples.fill(0))
}

export interface FakeAudioContextInit {
  readonly currentTime?: number
  readonly state?: AudioContextState
}

// A real AudioContext.currentTime is a monotonic clock the engine schedules
// future work against; a mock that freezes it can't distinguish "scheduled
// for later" from "already happened", which is exactly the class of bug
// this fake exists to catch. advance() is the only way to move it.
export class FakeAudioContext {
  static instances: FakeAudioContext[] = []

  readonly mediaNodes: FakeAudioNode[] = []
  readonly mediaElements: HTMLMediaElement[] = []
  readonly destination = new FakeAudioNode()
  state: AudioContextState
  private clockSeconds: number

  constructor(init: FakeAudioContextInit = {}) {
    this.clockSeconds = init.currentTime ?? 0
    this.state = init.state ?? "suspended"
    FakeAudioContext.instances.push(this)
  }

  get currentTime(): number {
    return this.clockSeconds
  }

  advance(seconds: number): void {
    if (seconds < 0) throw new RangeError("FakeAudioContext cannot advance by a negative duration")
    this.clockSeconds += seconds
  }

  resume = vi.fn(async () => {
    this.state = "running"
  })

  close = vi.fn(async () => {
    this.state = "closed"
  })

  createGain = vi.fn(() => new FakeAudioNode())
  createDynamicsCompressor = vi.fn(() => new FakeAudioNode())
  createBiquadFilter = vi.fn(() => new FakeAudioNode())
  createAnalyser = vi.fn(() => new FakeAudioNode())

  createMediaElementSource(element: HTMLMediaElement): FakeAudioNode {
    const node = new FakeAudioNode()
    this.mediaNodes.push(node)
    this.mediaElements.push(element)
    return node
  }

  decodeAudioData = vi.fn(
    async (): Promise<AudioBuffer> =>
      ({
        duration: 2,
        length: 4,
        numberOfChannels: 2,
        getChannelData: (channel: number) =>
          new Float32Array(channel === 0 ? [1, 2, 3, 4] : [5, 6, 7, 8]),
      }) as unknown as AudioBuffer
  )

  addEventListener = vi.fn()
  removeEventListener = vi.fn()
}

export function createFakeAudioContext(init: FakeAudioContextInit = {}): FakeAudioContext {
  return new FakeAudioContext(init)
}

export interface FakeStretchNodeInit {
  readonly inputTime?: number
  readonly rate?: number
  readonly active?: boolean
  // When true, a `when`/`output` timestamp already behind the fake's clock
  // throws instead of only being logged in scheduleLog — real Web Audio
  // just fires such calls immediately, which is usually a caller bug.
  readonly rejectPastSchedule?: boolean
}

export interface FakeStretchScheduleCall {
  readonly method: "start" | "stop" | "schedule"
  readonly when: number
  readonly contextTimeAtCall: number
  readonly isPast: boolean
}

export interface FakeStretchClock {
  readonly currentTime: number
}

export class FakeStretchNode {
  readonly scheduleLog: FakeStretchScheduleCall[] = []
  readonly port = { close: vi.fn() }

  private readonly clock: FakeStretchClock
  private readonly rejectPastSchedule: boolean
  private basePosition: number
  private scheduledAt: number
  private rate: number
  private active: boolean
  private loopStart = 0
  private loopEnd = 0

  constructor(clock: FakeStretchClock, init: FakeStretchNodeInit = {}) {
    this.clock = clock
    this.basePosition = init.inputTime ?? 0
    this.scheduledAt = clock.currentTime
    this.rate = init.rate ?? 1
    this.active = init.active ?? false
    this.rejectPastSchedule = init.rejectPastSchedule ?? false
  }

  get inputTime(): number {
    return this.projectPosition(this.clock.currentTime)
  }

  connect = vi.fn()
  disconnect = vi.fn()
  configure = vi.fn(async () => undefined)
  latency = vi.fn(async () => 0.12)
  addBuffers = vi.fn(async () => 2)
  dropBuffers = vi.fn(async (): Promise<StretchBufferExtent> => ({ start: 0, end: 0 }))
  setUpdateInterval = vi.fn(async () => undefined)

  schedule = vi.fn(
    async (change: StretchSchedule, _adjustPrevious?: boolean): Promise<StretchSchedule> => {
      const when = change.output ?? this.clock.currentTime
      this.record("schedule", when)
      this.applyBoundary(when, change.input)
      if (change.active !== undefined) this.active = change.active
      if (change.rate !== undefined) this.rate = change.rate
      if (change.loopStart !== undefined) this.loopStart = change.loopStart
      if (change.loopEnd !== undefined) this.loopEnd = change.loopEnd
      return change
    }
  )

  start = vi.fn(
    async (
      when?: number,
      offset?: number,
      _duration?: number,
      rate?: number,
      _semitones?: number
    ): Promise<StretchSchedule> => {
      const effectiveWhen = when ?? this.clock.currentTime
      this.record("start", effectiveWhen)
      this.applyBoundary(effectiveWhen, offset)
      this.active = true
      if (rate !== undefined) this.rate = rate
      return { active: true, input: this.basePosition, output: effectiveWhen, rate: this.rate }
    }
  )

  stop = vi.fn(async (when?: number): Promise<StretchSchedule> => {
    const effectiveWhen = when ?? this.clock.currentTime
    this.record("stop", effectiveWhen)
    this.applyBoundary(effectiveWhen, undefined)
    this.active = false
    return { active: false, input: this.basePosition, output: effectiveWhen, rate: this.rate }
  })

  private applyBoundary(when: number, explicitInput: number | undefined): void {
    this.basePosition = explicitInput ?? this.projectPosition(when)
    this.scheduledAt = when
  }

  private projectPosition(atTime: number): number {
    if (!this.active) return this.basePosition
    const elapsed = Math.max(0, atTime - this.scheduledAt)
    return this.basePosition + elapsed * this.rate
  }

  private record(method: FakeStretchScheduleCall["method"], when: number): void {
    const contextTimeAtCall = this.clock.currentTime
    const isPast = when < contextTimeAtCall
    this.scheduleLog.push({ method, when, contextTimeAtCall, isPast })
    if (isPast && this.rejectPastSchedule) {
      throw new RangeError(
        `FakeStretchNode.${method} received when=${when} already in the past (currentTime=${contextTimeAtCall})`
      )
    }
  }
}

export function createFakeStretchNode(
  clock: FakeStretchClock,
  init: FakeStretchNodeInit = {}
): StretchNode {
  return new FakeStretchNode(clock, init) as unknown as StretchNode
}
