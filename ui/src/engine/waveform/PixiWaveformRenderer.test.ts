import { beforeEach, describe, expect, it, vi } from "vitest"
import { PixiWaveformRenderer, type PixiLoader } from "./PixiWaveformRenderer"
import type { WaveformLevel, WaveformRendererInput } from "./types"

const resize = vi.fn()
const start = vi.fn()
const stop = vi.fn()
const destroy = vi.fn()
const tickerAdd = vi.fn()
const tickerRemove = vi.fn()
const stageAdd = vi.fn()
const graphicsClear = vi.fn()

class ApplicationStub {
  canvas = document.createElement("canvas")
  screen = { width: 800, height: 160 }
  stage = { addChild: stageAdd }
  ticker = { add: tickerAdd, remove: tickerRemove, maxFPS: 0 }
  resize = resize
  start = start
  stop = stop
  destroy = destroy
  init = vi.fn().mockResolvedValue(undefined)
}

class GraphicsStub {
  clear = graphicsClear.mockReturnThis()
  moveTo = vi.fn().mockReturnThis()
  lineTo = vi.fn().mockReturnThis()
  stroke = vi.fn().mockReturnThis()
}

class ResizeObserverStub {
  static disconnect = vi.fn()
  observe = vi.fn()
  disconnect = ResizeObserverStub.disconnect
}

function input(): WaveformRendererInput {
  const level: WaveformLevel = {
    bucketDurationSeconds: 0.1,
    minimum: new Int16Array(20).fill(-16_000),
    maximum: new Int16Array(20).fill(16_000),
    low: new Uint16Array(20).fill(10),
    mid: new Uint16Array(20).fill(20),
    high: new Uint16Array(20).fill(30),
  }
  return {
    timeline: { durationSeconds: 2, levels: [level] },
    viewport: {
      width: 800,
      height: 160,
      devicePixelRatio: 2,
      startSeconds: 0,
      endSeconds: 2,
    },
    playheadSeconds: 1,
    follow: true,
    palette: { low: 1, mid: 2, high: 3, playhead: 4 },
  }
}

function loader(app: ApplicationStub): PixiLoader {
  return vi.fn(async () => ({
    Application: class { constructor() { return app } },
    Graphics: GraphicsStub,
  })) as unknown as PixiLoader
}

describe("PixiWaveformRenderer lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal("ResizeObserver", ResizeObserverStub)
    Object.defineProperty(document, "hidden", { configurable: true, value: false })
  })

  it("destroys an application whose asynchronous initialization completes after cancellation", async () => {
    let finishInit!: () => void
    const app = new ApplicationStub()
    app.init = vi.fn(() => new Promise<void>((resolve) => { finishInit = resolve }))
    const container = document.createElement("div")
    const renderer = new PixiWaveformRenderer(container, input(), loader(app))

    const mounting = renderer.mount()
    await vi.waitFor(() => expect(app.init).toHaveBeenCalledTimes(1))
    renderer.destroy()
    finishInit()
    await mounting

    expect(destroy).toHaveBeenCalledWith(true, { children: true, texture: true, textureSource: true })
    expect(container.querySelector("canvas")).toBeNull()
    expect(tickerAdd).not.toHaveBeenCalled()
  })

  it("stops its private ticker while hidden and restarts when visible", async () => {
    const app = new ApplicationStub()
    const renderer = new PixiWaveformRenderer(document.createElement("div"), input(), loader(app))
    await renderer.mount()
    expect(app.ticker.maxFPS).toBe(60)
    expect(start).toHaveBeenCalledTimes(1)

    Object.defineProperty(document, "hidden", { configurable: true, value: true })
    document.dispatchEvent(new Event("visibilitychange"))
    expect(stop).toHaveBeenCalledTimes(1)

    Object.defineProperty(document, "hidden", { configurable: true, value: false })
    document.dispatchEvent(new Event("visibilitychange"))
    expect(start).toHaveBeenCalledTimes(2)
    renderer.destroy()
  })

  it("removes ticker work, observers, canvas and GPU resources on teardown", async () => {
    const app = new ApplicationStub()
    const container = document.createElement("div")
    const renderer = new PixiWaveformRenderer(container, input(), loader(app))
    await renderer.mount()
    expect(container.querySelector("canvas")).not.toBeNull()

    renderer.destroy()
    renderer.destroy()

    expect(tickerRemove).toHaveBeenCalledTimes(1)
    expect(ResizeObserverStub.disconnect).toHaveBeenCalledTimes(1)
    expect(destroy).toHaveBeenCalledTimes(1)
    expect(destroy).toHaveBeenCalledWith(true, { children: true, texture: true, textureSource: true })
  })
})
