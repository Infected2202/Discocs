import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import DjFader from "./DjFader"

describe("DjFader", () => {
  it("maps vertical pointer position to a clamped channel value and commits", () => {
    const onChange = vi.fn()
    const onCommit = vi.fn()
    render(<DjFader label="Deck A channel" value={0.5} onChange={onChange} onCommit={onCommit} />)
    const fader = screen.getByRole("slider", { name: "Deck A channel" })
    vi.spyOn(fader, "getBoundingClientRect").mockReturnValue({
      top: 0, left: 0, width: 30, height: 100, right: 30, bottom: 100, x: 0, y: 0, toJSON: () => ({}),
    })

    fireEvent.pointerDown(fader, { pointerId: 2, clientY: 25 })
    fireEvent.pointerUp(fader, { pointerId: 2, clientY: -50 })

    expect(onChange).toHaveBeenLastCalledWith(1)
    expect(onCommit).toHaveBeenCalledWith(1)
  })

  it("supports bipolar horizontal keyboard movement", () => {
    const onChange = vi.fn()
    const onCommit = vi.fn()
    render(
      <DjFader
        label="Crossfader"
        value={0}
        min={-1}
        max={1}
        orientation="horizontal"
        onChange={onChange}
        onCommit={onCommit}
      />,
    )

    fireEvent.keyDown(screen.getByRole("slider", { name: "Crossfader" }), { key: "ArrowLeft" })

    expect(onChange.mock.calls[0]?.[0]).toBeCloseTo(-0.02)
    expect(onCommit.mock.calls[0]?.[0]).toBeCloseTo(-0.02)
  })
})
