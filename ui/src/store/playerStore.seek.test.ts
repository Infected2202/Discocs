import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { playerPlayback as audioEngine } from "@/engine/playback"
import { usePlayerStore } from "./playerStore"

vi.mock("@/engine/playback", () => ({
  playerPlayback: {
    init: vi.fn(),
    load: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    seek: vi.fn(),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    setMediaSession: vi.fn(),
    registerMediaSessionHandlers: vi.fn(),
    consumePrefetched: vi.fn().mockReturnValue(null),
    prefetch: vi.fn().mockResolvedValue(undefined),
    cancelPrefetch: vi.fn(),
    clearPrefetched: vi.fn(),
  },
}))

describe("seek() — не откатывается к устаревшей позиции (A.5)", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    usePlayerStore.setState({ duration: 180, currentTime: 10 })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("отменяет отложенный throttled timeupdate, чтобы он не перезаписал currentTime после seek", () => {
    // Достаём callbacks, которые стор зарегистрировал в audioEngine.init() при создании.
    const { onTimeUpdate } = vi.mocked(audioEngine.init).mock.calls[0][0]

    // Первый вызов проходит сразу (leading edge throttle), второй остаётся
    // "отложенным" внутри окна ~250мс — именно он и стрелял бы уже ПОСЛЕ seek.
    onTimeUpdate(10, 180)
    onTimeUpdate(10.1, 180)

    usePlayerStore.getState().seek(0.5)
    expect(usePlayerStore.getState().currentTime).toBe(90)

    // Без throttledSetTime.cancel() этот тик откатил бы currentTime обратно к 10.1.
    vi.advanceTimersByTime(250)
    expect(usePlayerStore.getState().currentTime).toBe(90)
  })
})
