import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import type { TrackSummary } from "@/api/types"

// Minimal TimeRanges mock — enough for AudioEngine's buffered-range scan
class MockTimeRanges {
  private readonly ranges: Array<[number, number]>
  constructor(ranges: Array<[number, number]> = []) {
    this.ranges = ranges
  }
  get length() { return this.ranges.length }
  start(i: number) { return this.ranges[i][0] }
  end(i: number) { return this.ranges[i][1] }
}

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
  buffered: MockTimeRanges = new MockTimeRanges()

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
      onBufferUpdate: vi.fn(),
      onPlaybackStateChange: onStateChange,
      onEnded: vi.fn(),
      onError: vi.fn(),
    })

    engine.load("http://example.com/track1.flac")
    const newEl = audioInstances[audioInstances.length - 1]

    newEl.emit("play")
    expect(onStateChange).toHaveBeenCalledWith("playing")
  })

  it("сбрасывает buffered ranges сразу при load(), до того как новый элемент что-то скачал", async () => {
    const engine = await makeEngine()
    const onBufferUpdate = vi.fn()
    engine.init({
      onTimeUpdate: vi.fn(),
      onBufferUpdate,
      onPlaybackStateChange: vi.fn(),
      onEnded: vi.fn(),
      onError: vi.fn(),
    })

    engine.load("http://example.com/track1.flac")

    expect(onBufferUpdate).toHaveBeenCalledWith([])
  })

  it("сразу показывает полный буфер для подготовленного Blob", async () => {
    const engine = await makeEngine()
    const onBufferUpdate = vi.fn()
    engine.init({
      onTimeUpdate: vi.fn(),
      onBufferUpdate,
      onPlaybackStateChange: vi.fn(),
      onEnded: vi.fn(),
      onError: vi.fn(),
    })

    engine.load("blob:prepared", 7, "mp3-192", true)

    expect(onBufferUpdate).toHaveBeenCalledWith([{ start: 0, end: 1 }])
  })
})

describe("AudioEngine.resumeAtSeconds()", () => {
  it("перематывает сразу, если метаданные уже загружены", async () => {
    const engine = await makeEngine()
    engine.load("http://example.com/track1.flac")
    const el = audioInstances[audioInstances.length - 1]
    el.duration = 200

    engine.resumeAtSeconds(42)

    expect(el.currentTime).toBe(42)
  })

  it("откладывает перемотку до loadedmetadata, пока duration неизвестна", async () => {
    const engine = await makeEngine()
    engine.load("http://example.com/track1.flac")
    const el = audioInstances[audioInstances.length - 1]
    // duration ещё NaN — прямой seek был бы молча проигнорирован

    engine.resumeAtSeconds(42)
    expect(el.currentTime).toBe(0)

    el.duration = 200
    el.emit("loadedmetadata")
    expect(el.currentTime).toBe(42)
  })

  it("применяет отложенную перемотку только один раз", async () => {
    const engine = await makeEngine()
    engine.load("http://example.com/track1.flac")
    const el = audioInstances[audioInstances.length - 1]

    engine.resumeAtSeconds(42)
    el.duration = 200
    el.emit("loadedmetadata")
    el.currentTime = 100

    el.emit("loadedmetadata") // повторное событие не должно откатывать позицию
    expect(el.currentTime).toBe(100)
  })

  it("не влияет на следующий трек после load()", async () => {
    const engine = await makeEngine()
    engine.load("http://example.com/track1.flac")

    engine.resumeAtSeconds(42)
    engine.load("http://example.com/track2.flac")
    const next = audioInstances[audioInstances.length - 1]

    next.duration = 200
    next.emit("loadedmetadata")
    expect(next.currentTime).toBe(0)
  })
})

describe("AudioEngine — buffered reporting", () => {
  it("использует preload=auto для активной буферизации", async () => {
    const engine = await makeEngine()
    engine.load("http://example.com/track1.flac")

    expect(audioInstances.at(-1)?.preload).toBe("auto")
  })

  it("переводит buffered TimeRanges в доли duration", async () => {
    const engine = await makeEngine()
    const onBufferUpdate = vi.fn()
    engine.init({
      onTimeUpdate: vi.fn(),
      onBufferUpdate,
      onPlaybackStateChange: vi.fn(),
      onEnded: vi.fn(),
      onError: vi.fn(),
    })

    engine.load("http://example.com/track1.flac")
    const el = audioInstances[audioInstances.length - 1]
    el.duration = 200
    el.currentTime = 10
    el.buffered = new MockTimeRanges([[0, 50]])

    el.emit("progress")

    expect(onBufferUpdate).toHaveBeenCalledWith([{ start: 0, end: 0.25 }])
  })

  it("сохраняет раздельные диапазоны после перемотки", async () => {
    const engine = await makeEngine()
    const onBufferUpdate = vi.fn()
    engine.init({
      onTimeUpdate: vi.fn(),
      onBufferUpdate,
      onPlaybackStateChange: vi.fn(),
      onEnded: vi.fn(),
      onError: vi.fn(),
    })

    engine.load("http://example.com/track1.flac")
    const el = audioInstances[audioInstances.length - 1]
    el.duration = 100
    el.currentTime = 80
    el.buffered = new MockTimeRanges([[0, 20], [70, 90]])

    el.emit("progress")

    expect(onBufferUpdate).toHaveBeenCalledWith([
      { start: 0, end: 0.2 },
      { start: 0.7, end: 0.9 },
    ])
  })

  it("не вызывает onBufferUpdate пока duration неизвестна (NaN)", async () => {
    const engine = await makeEngine()
    const onBufferUpdate = vi.fn()
    engine.init({
      onTimeUpdate: vi.fn(),
      onBufferUpdate,
      onPlaybackStateChange: vi.fn(),
      onEnded: vi.fn(),
      onError: vi.fn(),
    })

    engine.load("http://example.com/track1.flac")
    const el = audioInstances[audioInstances.length - 1]
    onBufferUpdate.mockClear() // сбросить вызов reset-в-0 из load()
    el.buffered = new MockTimeRanges([[0, 50]])

    el.emit("progress")

    expect(onBufferUpdate).not.toHaveBeenCalled()
  })

  it("один раз сообщает о полном покрытии duration с допуском", async () => {
    const engine = await makeEngine()
    const onFullyBuffered = vi.fn()
    engine.init({
      onTimeUpdate: vi.fn(), onBufferUpdate: vi.fn(), onFullyBuffered,
      onPlaybackStateChange: vi.fn(), onEnded: vi.fn(), onError: vi.fn(),
    })
    engine.load("http://example.com/track1.flac", 7, "mp3-192")
    const el = audioInstances.at(-1)!
    el.duration = 100
    el.buffered = new MockTimeRanges([[0, 99.8]])

    el.emit("progress")
    el.emit("canplaythrough")

    expect(onFullyBuffered).toHaveBeenCalledTimes(1)
    expect(onFullyBuffered).toHaveBeenCalledWith(7, "mp3-192")
  })

  it("не считает буфер полным при разрыве TimeRanges", async () => {
    const engine = await makeEngine()
    const onFullyBuffered = vi.fn()
    engine.init({
      onTimeUpdate: vi.fn(), onBufferUpdate: vi.fn(), onFullyBuffered,
      onPlaybackStateChange: vi.fn(), onEnded: vi.fn(), onError: vi.fn(),
    })
    engine.load("http://example.com/track1.flac", 7, "raw")
    const el = audioInstances.at(-1)!
    el.duration = 100
    el.buffered = new MockTimeRanges([[0, 50], [60, 100]])

    el.emit("progress")

    expect(onFullyBuffered).not.toHaveBeenCalled()
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("AudioEngine — Blob prefetch", () => {
  it("загружает следующий трек один раз и отдаёт object URL только совпадающему профилю", async () => {
    const engine = await makeEngine()
    const createObjectURL = vi.fn().mockReturnValue("blob:next")
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      blob: vi.fn().mockResolvedValue(new Blob(["audio"])),
    }))
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL: vi.fn() })

    await engine.prefetch(2, "/audio/2", "mp3-192")
    await engine.prefetch(2, "/audio/2", "mp3-192")

    expect(fetch).toHaveBeenCalledTimes(1)
    expect(engine.consumePrefetched(2, "raw")).toBeNull()
    expect(engine.consumePrefetched(2, "mp3-192")).toBe("blob:next")
  })
})

describe("AudioEngine.setMediaSession()", () => {
  let metadataArgs: MediaMetadataInit | undefined
  const setActionHandler = vi.fn()

  const track: TrackSummary = {
    id: 1,
    title: "Pattern 4 (LJ Kruzer mix)",
    artists: [{ id: 1, name: "Cyan341" }],
    duration: 300,
    release: { id: 1, title: "Some EP" },
    artwork: { url: "/api/v1/tracks/1/cover", source: "local", placeholder: false },
    explicit: false,
    liked: false,
    actions: [],
  }

  beforeEach(() => {
    metadataArgs = undefined
    setActionHandler.mockClear()
    vi.stubGlobal(
      "MediaMetadata",
      class {
        constructor(init: MediaMetadataInit) {
          metadataArgs = init
        }
      }
    )
    Object.defineProperty(navigator, "mediaSession", {
      value: { setActionHandler },
      configurable: true,
    })
  })

  it("не указывает type у artwork — content-type исходника из Navidrome заранее неизвестен, и браузер молча роняет artwork при несовпадении", async () => {
    const engine = await makeEngine()
    engine.setMediaSession(track, "/api/v1/tracks/1/cover?size=512")

    expect(metadataArgs?.artwork).toEqual([
      { src: "/api/v1/tracks/1/cover?size=512", sizes: "512x512" },
    ])
  })

  it("artwork — пустой массив, если url не передан", async () => {
    const engine = await makeEngine()
    engine.setMediaSession(track, undefined)

    expect(metadataArgs?.artwork).toEqual([])
  })

  it("title/artist/album берутся из трека", async () => {
    const engine = await makeEngine()
    engine.setMediaSession(track, undefined)

    expect(metadataArgs?.title).toBe("Pattern 4 (LJ Kruzer mix)")
    expect(metadataArgs?.artist).toBe("Cyan341")
    expect(metadataArgs?.album).toBe("Some EP")
  })
})

describe("AudioEngine.clear()", () => {
  it("unloads personal media and returns callbacks to idle", async () => {
    const engine = await makeEngine()
    const onTimeUpdate = vi.fn()
    const onBufferUpdate = vi.fn()
    const onPlaybackStateChange = vi.fn()
    engine.init({
      onTimeUpdate,
      onBufferUpdate,
      onPlaybackStateChange,
      onEnded: vi.fn(),
      onError: vi.fn(),
    })
    engine.load("http://example.com/private-track.flac")
    const active = audioInstances[audioInstances.length - 1]

    engine.clear()

    expect(active.pause).toHaveBeenCalled()
    expect(active.src).toBe("")
    expect(active.load).toHaveBeenCalled()
    expect(audioInstances).toHaveLength(3)
    expect(onTimeUpdate).toHaveBeenCalledWith(0, 0)
    expect(onBufferUpdate).toHaveBeenCalledWith([])
    expect(onPlaybackStateChange).toHaveBeenCalledWith("idle")
  })
})
