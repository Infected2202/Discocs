import { beforeEach, describe, expect, it, vi } from "vitest"
import {
  loadPixiWithCspSupport,
  PixiWaveformRenderer,
  type PixiLoader,
  type PixiModule,
} from "./PixiWaveformRenderer"
import type { WaveformLevel, WaveformRendererInput } from "./types"

const resize = vi.fn()
const start = vi.fn()
const stop = vi.fn()
const destroy = vi.fn()
const renderFrame = vi.fn()
const tickerAdd = vi.fn()
const tickerRemove = vi.fn()
const stageAdd = vi.fn()
const graphicsClear = vi.fn()
const graphicsStroke = vi.fn()

class ApplicationStub {
  canvas = document.createElement("canvas")
  screen = { width: 800, height: 160 }
  stage = { addChild: stageAdd }
  ticker = { add: tickerAdd, remove: tickerRemove, maxFPS: 0 }
  resize = resize
  start = start
  stop = stop
  destroy = destroy
  render = renderFrame
  init = vi.fn().mockResolvedValue(undefined)
}

class GraphicsStub {
  clear = graphicsClear.mockReturnThis()
  moveTo = vi.fn().mockReturnThis()
  lineTo = vi.fn().mockReturnThis()
  stroke = graphicsStroke.mockReturnThis()
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
    timeline: { durationSeconds: 2, levels: [level], beats: new Float32Array([0.5, 1.5]) },
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

  it("installs Pixi CSP support before importing the renderer", async () => {
    const order: string[] = []
    const pixiModule = {
      Application: ApplicationStub,
      Graphics: GraphicsStub,
    } as unknown as PixiModule

    const loaded = await loadPixiWithCspSupport(
      async () => { order.push("csp") },
      async () => { order.push("pixi"); return pixiModule },
    )

    expect(order).toEqual(["csp", "pixi"])
    expect(loaded).toBe(pixiModule)
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

  it("renders only when mounted or given changed input", async () => {
    const app = new ApplicationStub()
    const renderer = new PixiWaveformRenderer(document.createElement("div"), input(), loader(app))
    await renderer.mount()
    expect(renderFrame).toHaveBeenCalledTimes(1)
    expect(start).not.toHaveBeenCalled()
    expect(tickerAdd).not.toHaveBeenCalled()

    const unchanged = input()
    renderer.update(unchanged)
    expect(renderFrame).toHaveBeenCalledTimes(2)
    renderer.update(unchanged)
    expect(renderFrame).toHaveBeenCalledTimes(2)

    renderer.destroy()
  })

  it("redraws only the playhead when an overview viewport stays fixed", async () => {
    const app = new ApplicationStub()
    const initial = input()
    const renderer = new PixiWaveformRenderer(document.createElement("div"), initial, loader(app))
    await renderer.mount()
    graphicsClear.mockClear()
    graphicsStroke.mockClear()

    renderer.update({ ...initial, playheadSeconds: 1.2 })

    expect(graphicsClear).toHaveBeenCalledTimes(1)
    expect(graphicsStroke).toHaveBeenCalledTimes(1)
    expect(graphicsStroke).toHaveBeenCalledWith({ color: 4, width: 2 })
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

    expect(tickerRemove).not.toHaveBeenCalled()
    expect(ResizeObserverStub.disconnect).toHaveBeenCalledTimes(1)
    expect(destroy).toHaveBeenCalledTimes(1)
    expect(destroy).toHaveBeenCalledWith(true, { children: true, texture: true, textureSource: true })
  })

  it("draws beat events from the offline timeline artifact", async () => {
    const app = new ApplicationStub()
    const renderer = new PixiWaveformRenderer(document.createElement("div"), input(), loader(app))

    await renderer.mount()

    expect(graphicsStroke).toHaveBeenCalledWith({ color: 0xffffff, width: 1, alpha: 0.2 })
    renderer.destroy()
  })
})
