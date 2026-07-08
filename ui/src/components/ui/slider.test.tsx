import { describe, expect, it, vi } from "vitest"
import { render } from "@testing-library/react"

import { Slider } from "./slider"

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal("ResizeObserver", ResizeObserverStub)

describe("Slider", () => {
  it("renders thumbs from value when it is provided", () => {
    const { container } = render(<Slider value={[25]} />)

    expect(container.querySelectorAll('[data-slot="slider-thumb"]')).toHaveLength(1)
  })

  it("renders thumbs from defaultValue when value is absent", () => {
    const { container } = render(<Slider defaultValue={[10, 90]} />)

    expect(container.querySelectorAll('[data-slot="slider-thumb"]')).toHaveLength(2)
  })

  it("falls back to min/max when neither value nor defaultValue is provided", () => {
    const { container } = render(<Slider min={10} max={50} />)

    expect(container.querySelectorAll('[data-slot="slider-thumb"]')).toHaveLength(2)
  })
})
