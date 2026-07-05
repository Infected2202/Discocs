import { describe, it, expect, vi, beforeEach } from "vitest"

// Minimal HTMLAudioElement mock
class MockAudio {
  src = ""
  preload = ""
  volume = 1
  muted = false
  currentTime = 0
  duration = Number.NaN
  paused = true
  ended = false
  error: MediaError | null = null

  private listeners: Record<string, Array<() => void>> = {}

  addEventListener(event: string, handler: () => void) {
    this.listeners[event] ??= []
    this.listeners[event].push(handler)
  }

  removeEventListener(event: string, handler: () => void) {
    if (!this.listeners[event]) return
    this.listeners[event] = this.listeners[event].filter((h) => h !== handler)
  }

  load = vi.fn()
  play = vi.fn().mockResolvedValue(undefined)
  pause = vi.fn()

  emit(event: string) {
    this.listeners[event]?.forEach((h) => h())
  }
}

const audioInstances: MockAudio[] = []

beforeEach(() => {
  audioInstances.length = 0
  vi.stubGlobal("Audio", function () {
    const instance = new MockAudio()
    audioInstances.push(instance)
    return instance
  })
})

// Dynamic import чтобы каждый тест получал свежий модуль с мокированным Audio
async function makeEngine() {
  vi.resetModules()
  const mod = await import("./AudioEngine")
  return mod.audioEngine
}

describe("AudioEngine.load()", () => {
  it("создаёт новый Audio элемент при каждом вызове load()", async () => {
    const engine = await makeEngine()
    expect(audioInstances).toHaveLength(1) // конструктор

    engine.load("http://example.com/track1.flac")
    expect(audioInstances).toHaveLength(2) // load создал новый

    engine.load("http://example.com/track2.flac")
    expect(audioInstances).toHaveLength(3) // ещё один новый
  })

  it("очищает src предыдущего элемента при смене трека", async () => {
    const engine = await makeEngine()
    engine.load("http://example.com/track1.flac")

    const prev = audioInstances[1]
    expect(prev).toBeDefined()

    engine.load("http://example.com/track2.flac")

    // Предыдущий элемент должен быть очищен
    expect(prev.src).toBe("")
    expect(prev.load).toHaveBeenCalled()
  })

  it("переносит volume и muted на новый элемент", async () => {
    const engine = await makeEngine()
    engine.setVolume(0.7)
    engine.setMuted(true)

    engine.load("http://example.com/track1.flac")

    const newEl = audioInstances[audioInstances.length - 1]
    expect(newEl.volume).toBe(0.7)
    expect(newEl.muted).toBe(true)
  })

  it("новый элемент получает правильный src", async () => {
    const engine = await makeEngine()
    engine.load("http://example.com/track1.flac")

    const newEl = audioInstances[audioInstances.length - 1]
    expect(newEl.src).toBe("http://example.com/track1.flac")
  })

  it("callbacks работают на новом элементе после load()", async () => {
    const engine = await makeEngine()
    const onStateChange = vi.fn()
    engine.init({
      onTimeUpdate: vi.fn(),
      onPlaybackStateChange: onStateChange,
      onEnded: vi.fn(),
      onError: vi.fn(),
    })

    engine.load("http://example.com/track1.flac")
    const newEl = audioInstances[audioInstances.length - 1]

    newEl.emit("play")
    expect(onStateChange).toHaveBeenCalledWith("playing")
  })
})
