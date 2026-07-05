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
