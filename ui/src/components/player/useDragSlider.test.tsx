import { fireEvent, render } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { useDragSlider } from "./useDragSlider"

function Slider({ onChange, onCommit }: {
  onChange: (value: number) => void
  onCommit: (value: number) => void
}) {
  const { trackRef, handlePointerDown } = useDragSlider({ onChange, onCommit })
  return <div ref={trackRef} onPointerDown={handlePointerDown} data-testid="slider" />
}

describe("useDragSlider", () => {
  it("перематывает touch/pen/mouse через единые pointer events", () => {
    const onChange = vi.fn()
    const onCommit = vi.fn()
    const { getByTestId } = render(<Slider onChange={onChange} onCommit={onCommit} />)
    const slider = getByTestId("slider")
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({
      left: 100,
      width: 200,
      right: 300,
      top: 0,
      bottom: 10,
      height: 10,
      x: 100,
      y: 0,
      toJSON: () => ({}),
    })
    slider.setPointerCapture = vi.fn()

    fireEvent.pointerDown(slider, { pointerId: 7, pointerType: "touch", clientX: 140 })
    fireEvent.pointerMove(window, { pointerId: 7, pointerType: "touch", clientX: 220 })
    fireEvent.pointerUp(window, { pointerId: 7, pointerType: "touch", clientX: 0 })

    expect(slider.setPointerCapture).toHaveBeenCalledWith(7)
    expect(onChange).toHaveBeenNthCalledWith(1, 0.2)
    expect(onChange).toHaveBeenNthCalledWith(2, 0.6)
    expect(onCommit).toHaveBeenCalledOnce()
    expect(onCommit).toHaveBeenCalledWith(0.6)
  })

  it("коммитит позицию тапа без обязательного pointermove", () => {
    const onCommit = vi.fn()
    const { getByTestId } = render(<Slider onChange={vi.fn()} onCommit={onCommit} />)
    const slider = getByTestId("slider")
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({
      left: 100, width: 200, right: 300, top: 0, bottom: 10,
      height: 10, x: 100, y: 0, toJSON: () => ({}),
    })
    slider.setPointerCapture = vi.fn()

    fireEvent.pointerDown(slider, { pointerId: 5, clientX: 150 })
    fireEvent.pointerUp(window, { pointerId: 5, clientX: 0 })

    expect(onCommit).toHaveBeenCalledWith(0.25)
  })

  it("не коммитит случайное положение при pointercancel", () => {
    const onCommit = vi.fn()
    const { getByTestId } = render(<Slider onChange={vi.fn()} onCommit={onCommit} />)
    const slider = getByTestId("slider")
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({
      left: 0, width: 100, right: 100, top: 0, bottom: 10,
      height: 10, x: 0, y: 0, toJSON: () => ({}),
    })
    slider.setPointerCapture = vi.fn()

    fireEvent.pointerDown(slider, { pointerId: 9, clientX: 50 })
    fireEvent.pointerCancel(window, { pointerId: 9, clientX: 0 })

    expect(onCommit).not.toHaveBeenCalled()
  })
})
