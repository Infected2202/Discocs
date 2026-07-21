import { fireEvent, render } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import type { WaveformRendererInput } from "./types"

const renderer = vi.hoisted(() => ({ mount: vi.fn(), update: vi.fn(), destroy: vi.fn() }))
vi.mock("./PixiWaveformRenderer", () => ({
  PixiWaveformRenderer: class {
    mount = renderer.mount
    update = renderer.update
    destroy = renderer.destroy
  },
}))

import { WaveformSurface } from "./WaveformSurface"

const input = (): WaveformRendererInput => ({
  timeline: {
    durationSeconds: 100,
    levels: [],
    beats: new Float32Array(),
  },
  viewport: {
    width: 1_000,
    height: 100,
    devicePixelRatio: 1,
    startSeconds: 40,
    endSeconds: 60,
  },
  playheadSeconds: 50,
  follow: true,
  palette: { low: 1, mid: 2, high: 3, playhead: 4 },
})

function surface(interaction: "absolute" | "tape", onSeek: (seconds: number) => void): HTMLDivElement {
  const { container } = render(<WaveformSurface input={input()} interaction={interaction} onSeek={onSeek} />)
  const element = container.firstElementChild as HTMLDivElement
  element.setPointerCapture = vi.fn()
  element.getBoundingClientRect = () => ({
    x: 0, y: 0, left: 0, top: 0, right: 1_000, bottom: 100, width: 1_000, height: 100,
    toJSON: () => ({}),
  })
  return element
}

describe("WaveformSurface pointer interaction", () => {
  it("drags the overview cursor continuously with pointer capture", () => {
    const onSeek = vi.fn()
    const element = surface("absolute", onSeek)

    fireEvent.pointerDown(element, { pointerId: 7, clientX: 250 })
    fireEvent.pointerMove(element, { pointerId: 7, clientX: 750 })
    fireEvent.pointerUp(element, { pointerId: 7, clientX: 0 })

    expect(element.setPointerCapture).toHaveBeenCalledWith(7)
    expect(onSeek).toHaveBeenNthCalledWith(1, 45)
    expect(onSeek).toHaveBeenNthCalledWith(2, 55)
  })

  it("moves detailed waveform tape relative to its fixed centre playhead", () => {
    const onSeek = vi.fn()
    const element = surface("tape", onSeek)

    fireEvent.pointerDown(element, { pointerId: 8, clientX: 500 })
    fireEvent.pointerMove(element, { pointerId: 8, clientX: 400 })
    fireEvent.pointerUp(element, { pointerId: 8, clientX: 0 })

    expect(onSeek).toHaveBeenCalledTimes(1)
    expect(onSeek).toHaveBeenCalledWith(52)
  })

  it("keeps click-to-seek on the detailed waveform", () => {
    const onSeek = vi.fn()
    const element = surface("tape", onSeek)

    fireEvent.pointerDown(element, { pointerId: 9, clientX: 250 })
    fireEvent.pointerUp(element, { pointerId: 9, clientX: 0 })

    expect(onSeek).toHaveBeenCalledWith(45)
  })
})
