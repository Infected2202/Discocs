import { describe, it, expect } from "vitest"
import { render, screen, act } from "@testing-library/react"
import { useRef } from "react"
import { usePlayerStore } from "@/store/playerStore"
import {
  SeekIndicators,
  TimeReadout,
  QueueItemLiveTime,
  formatTime,
} from "./PlaybackProgress"

function setTime(currentTime: number, duration: number) {
  act(() => {
    usePlayerStore.getState()._setTime(currentTime, duration)
  })
}

function setBuffered(ranges: Array<{ start: number; end: number }>) {
  act(() => {
    usePlayerStore.getState()._setBuffered(ranges)
  })
}

function setNextTrackBuffer(info: { trackId: number; queueItemId: string | null; ready: boolean } | null) {
  act(() => {
    usePlayerStore.getState()._setNextTrackBuffer(info)
  })
}

function setSeekBuffering(active: boolean) {
  act(() => {
    usePlayerStore.getState()._setSeekBuffering(active)
  })
}

describe("formatTime", () => {
  it("форматирует секунды в m:ss", () => {
    expect(formatTime(0)).toBe("0:00")
    expect(formatTime(9)).toBe("0:09")
    expect(formatTime(75)).toBe("1:15")
  })

  it("нечисловые/отрицательные → 0:00", () => {
    expect(formatTime(Number.NaN)).toBe("0:00")
    expect(formatTime(-5)).toBe("0:00")
  })
})

describe("TimeReadout", () => {
  it("inline рендерит currentTime / duration и обновляется на тик", () => {
    setTime(30, 200)
    render(<TimeReadout variant="inline" />)
    expect(screen.getByText("0:30 / 3:20")).toBeInTheDocument()

    setTime(45, 200)
    expect(screen.getByText("0:45 / 3:20")).toBeInTheDocument()
  })

  it("split рендерит два отдельных значения", () => {
    setTime(61, 130)
    render(<TimeReadout variant="split" />)
    expect(screen.getByText("1:01")).toBeInTheDocument()
    expect(screen.getByText("2:10")).toBeInTheDocument()
  })
})

describe("SeekIndicators", () => {
  it("ширина заливки = currentTime / duration", () => {
    setTime(50, 200)
    const { container } = render(
      <SeekIndicators fillClassName="fill" thumbClassName="thumb" />
    )
    const fill = container.querySelector(".fill") as HTMLElement
    expect(fill.style.width).toBe("25%")
  })

  it("override перебивает живой прогресс", () => {
    setTime(50, 200) // 25% живого
    const { container } = render(
      <SeekIndicators fillClassName="fill" thumbClassName="thumb" override={0.8} />
    )
    const fill = container.querySelector(".fill") as HTMLElement
    expect(fill.style.width).toBe("80%")
  })

  it("duration = 0 → ширина 0%", () => {
    setTime(50, 0)
    const { container } = render(
      <SeekIndicators fillClassName="fill" thumbClassName="thumb" />
    )
    const fill = container.querySelector(".fill") as HTMLElement
    expect(fill.style.width).toBe("0%")
  })

  it("без bufferedClassName полоска буфера не рендерится", () => {
    setBuffered([{ start: 0, end: 0.6 }])
    const { container } = render(
      <SeekIndicators fillClassName="fill" thumbClassName="thumb" />
    )
    expect(container.querySelector(".buffered")).not.toBeInTheDocument()
  })

  it("рисует каждый диапазон буфера без закрашивания разрывов", () => {
    setTime(50, 200) // 25% живого прогресса
    setBuffered([{ start: 0, end: 0.2 }, { start: 0.7, end: 0.9 }])
    const { container } = render(
      <SeekIndicators
        fillClassName="fill"
        thumbClassName="thumb"
        bufferedClassName="buffered"
        override={0.8}
      />
    )
    const buffered = [...container.querySelectorAll<HTMLElement>(".buffered")]
    const fill = container.querySelector(".fill") as HTMLElement
    expect(buffered).toHaveLength(2)
    expect(buffered[0].style.left).toBe("0%")
    expect(buffered[0].style.width).toBe("20%")
    expect(buffered[1].style.left).toBe("70%")
    expect(Number.parseFloat(buffered[1].style.width)).toBeCloseTo(20)
    expect(fill.style.width).toBe("80%")
  })

  it("без nextBufferDotClassName кружок следующего трека не рендерится, даже если он есть в сторе", () => {
    setNextTrackBuffer({ trackId: 8, queueItemId: "queue-8", ready: false })
    const { container } = render(
      <SeekIndicators fillClassName="fill" thumbClassName="thumb" />
    )
    expect(container.querySelector("[data-next-buffer-dot]")).not.toBeInTheDocument()
  })

  it("nextTrackBuffer === null → кружок следующего трека не рендерится", () => {
    setNextTrackBuffer(null)
    const { container } = render(
      <SeekIndicators fillClassName="fill" thumbClassName="thumb" nextBufferDotClassName="next-dot" />
    )
    expect(container.querySelector("[data-next-buffer-dot]")).not.toBeInTheDocument()
  })

  it("следующий трек ещё грузится → кружок мигает (animate-pulse)", () => {
    setNextTrackBuffer({ trackId: 8, queueItemId: "queue-8", ready: false })
    const { container } = render(
      <SeekIndicators fillClassName="fill" thumbClassName="thumb" nextBufferDotClassName="next-dot" />
    )
    const dot = container.querySelector("[data-next-buffer-dot]") as HTMLElement
    expect(dot).toBeInTheDocument()
    expect(dot.dataset.ready).toBe("false")
    expect(dot.className).toContain("animate-pulse")
  })

  it("следующий трек полностью забуферен → кружок статичный, без мигания", () => {
    setNextTrackBuffer({ trackId: 8, queueItemId: "queue-8", ready: true })
    const { container } = render(
      <SeekIndicators fillClassName="fill" thumbClassName="thumb" nextBufferDotClassName="next-dot" />
    )
    const dot = container.querySelector("[data-next-buffer-dot]") as HTMLElement
    expect(dot).toBeInTheDocument()
    expect(dot.dataset.ready).toBe("true")
    expect(dot.className).not.toContain("animate-pulse")
  })

  it("seekBuffering === false → кружок ожидания seek не рендерится", () => {
    setSeekBuffering(false)
    const { container } = render(
      <SeekIndicators fillClassName="fill" thumbClassName="thumb" seekBufferDotClassName="seek-dot" />
    )
    expect(container.querySelector("[data-seek-buffer-dot]")).not.toBeInTheDocument()
  })

  it("seekBuffering === true → мигающий кружок рендерится в позиции текущего прогресса", () => {
    setTime(50, 200) // 25%
    setSeekBuffering(true)
    const { container } = render(
      <SeekIndicators fillClassName="fill" thumbClassName="thumb" seekBufferDotClassName="seek-dot" />
    )
    const dot = container.querySelector("[data-seek-buffer-dot]") as HTMLElement
    expect(dot).toBeInTheDocument()
    expect(dot.className).toContain("animate-pulse")
    expect(dot.style.left).toBe("calc(25% - 3px)")
  })
})

describe("QueueItemLiveTime", () => {
  it("рендерит и обновляет живое время", () => {
    setTime(12, 100)
    const { rerender } = render(<QueueItemLiveTime />)
    expect(screen.getByText("0:12 / 1:40")).toBeInTheDocument()
    setTime(13, 100)
    rerender(<QueueItemLiveTime />)
    expect(screen.getByText("0:13 / 1:40")).toBeInTheDocument()
  })
})

// Ключевой тест: родитель, не подписанный на currentTime, НЕ перерисовывается
// на тик — перерисовывается только лист. Падает, если подписку вернут наверх.
describe("изоляция ре-рендеров", () => {
  it("родитель с TimeReadout не ре-рендерится на смену currentTime", () => {
    let parentRenders = 0

    function Parent() {
      const renders = useRef(0)
      renders.current += 1
      parentRenders = renders.current
      return (
        <div>
          <span data-testid="static">static</span>
          <TimeReadout variant="inline" />
        </div>
      )
    }

    setTime(0, 200)
    render(<Parent />)
    const after1 = parentRenders

    setTime(10, 200)
    setTime(20, 200)
    setTime(30, 200)

    // Лист обновился...
    expect(screen.getByText("0:30 / 3:20")).toBeInTheDocument()
    // ...а родитель — нет.
    expect(parentRenders).toBe(after1)
  })
})
