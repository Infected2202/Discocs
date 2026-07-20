import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import DjKnob from "./DjKnob"

describe("DjKnob", () => {
  it("renders an empty value arc at the control's neutral default", () => {
    render(<DjKnob label="Deck A gain" value={0.5} defaultValue={0.5} />)

    expect(screen.getByTestId("knob-value-arc")).toHaveStyle({
      "--knob-arc-start": "135deg",
      "--knob-arc-end": "135deg",
    })
  })

  it("fills the arc between the neutral default and the current position", () => {
    render(<DjKnob label="Deck A gain" value={0.25} defaultValue={0.5} />)

    expect(screen.getByTestId("knob-value-arc")).toHaveStyle({
      "--knob-arc-start": "67.5deg",
      "--knob-arc-end": "135deg",
    })
  })

  it("fills toward the pointer on the other side of neutral", () => {
    render(<DjKnob label="Deck A gain" value={0.75} defaultValue={0.5} />)

    expect(screen.getByTestId("knob-value-arc")).toHaveStyle({
      "--knob-arc-start": "135deg",
      "--knob-arc-end": "202.5deg",
    })
  })

  it("maps upward drag to a clamped value and commits on release", () => {
    const onChange = vi.fn()
    const onCommit = vi.fn()
    render(<DjKnob label="Deck A filter" value={0} min={-1} max={1} onChange={onChange} onCommit={onCommit} />)
    const knob = screen.getByRole("slider", { name: "Deck A filter" })

    fireEvent.pointerDown(knob, { pointerId: 4, clientY: 120 })
    fireEvent.pointerMove(knob, { pointerId: 4, clientY: -120 })
    fireEvent.pointerUp(knob, { pointerId: 4, clientY: -120 })

    expect(onChange).toHaveBeenLastCalledWith(1)
    expect(onCommit).toHaveBeenCalledWith(1)
  })

  it("supports keyboard adjustment and a double-click neutral reset", () => {
    const onCommit = vi.fn()
    render(<DjKnob label="Deck B low" value={0.4} defaultValue={0.8} onChange={vi.fn()} onCommit={onCommit} />)
    const knob = screen.getByRole("slider", { name: "Deck B low" })

    fireEvent.keyDown(knob, { key: "ArrowUp" })
    expect(onCommit.mock.calls[0]?.[0]).toBeCloseTo(0.41)

    fireEvent.doubleClick(knob)
    expect(onCommit).toHaveBeenLastCalledWith(0.8)
  })
})
