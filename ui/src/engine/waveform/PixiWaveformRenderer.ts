import type { Application, Graphics } from "pixi.js"
import { selectWaveformLevel } from "./geometry"
import type { WaveformRendererInput } from "./types"

interface PixiModule {
  readonly Application: new () => Application
  readonly Graphics: new () => Graphics
}

export type PixiLoader = () => Promise<PixiModule>

const loadPixi: PixiLoader = async () => import("pixi.js")
const EMPTY_EVENTS = new Float32Array()

function energyColour(input: WaveformRendererInput, low: number, mid: number, high: number): number {
  if (low >= mid && low >= high) return input.palette.low
  if (mid >= high) return input.palette.mid
  return input.palette.high
}

function firstEventAtOrAfter(events: Float32Array, seconds: number): number {
  let low = 0
  let high = events.length
  while (low < high) {
    const middle = (low + high) >>> 1
    if (events[middle]! < seconds) low = middle + 1
    else high = middle
  }
  return low
}

export class PixiWaveformRenderer {
  private readonly container: HTMLElement
  private readonly loader: PixiLoader
  private input: WaveformRendererInput
  private app: Application | null = null
  private graphics: Graphics | null = null
  private cancelled = false
  private dirty = true
  private resizeObserver: ResizeObserver | null = null
  private readonly onVisibilityChange: () => void

  constructor(container: HTMLElement, input: WaveformRendererInput, loader: PixiLoader = loadPixi) {
    this.container = container
    this.input = input
    this.loader = loader
    this.onVisibilityChange = () => this.setVisible(!document.hidden && !this.container.hidden)
  }

  async mount(): Promise<void> {
    const { Application, Graphics } = await this.loader()
    if (this.cancelled) return

    const app = new Application()
    await app.init({
      autoDensity: true,
      autoStart: false,
      antialias: false,
      backgroundAlpha: 0,
      preference: "webgl",
      resolution: Math.max(1, this.input.viewport.devicePixelRatio),
      resizeTo: this.container,
      sharedTicker: false,
    })
    if (this.cancelled) {
      app.destroy(true, { children: true, texture: true, textureSource: true })
      return
    }

    this.app = app
    this.graphics = new Graphics()
    app.stage.addChild(this.graphics)
    app.ticker.maxFPS = 60
    app.ticker.add(this.onTick)
    app.canvas.style.cssText = "display:block;width:100%;height:100%;touch-action:none"
    this.container.appendChild(app.canvas)

    this.resizeObserver = new ResizeObserver(() => {
      app.resize()
      this.draw()
    })
    this.resizeObserver.observe(this.container)
    document.addEventListener("visibilitychange", this.onVisibilityChange)
    this.draw()
    this.setVisible(!document.hidden && !this.container.hidden)
  }

  update(input: WaveformRendererInput): void {
    this.input = input
    this.dirty = true
    this.draw()
  }

  setVisible(visible: boolean): void {
    if (!this.app) return
    if (visible) this.app.start()
    else this.app.stop()
  }

  destroy(): void {
    if (this.cancelled) return
    this.cancelled = true
    document.removeEventListener("visibilitychange", this.onVisibilityChange)
    this.resizeObserver?.disconnect()
    this.resizeObserver = null
    if (!this.app) return
    this.app.ticker.remove(this.onTick)
    this.app.stop()
    this.app.destroy(true, { children: true, texture: true, textureSource: true })
    this.app = null
    this.graphics = null
  }

  private readonly draw = (): void => {
    const graphics = this.graphics
    const app = this.app
    if (!graphics || !app) return
    const { timeline, viewport, playheadSeconds } = this.input
    const width = Math.max(1, app.screen.width)
    const height = Math.max(1, app.screen.height)
    const level = selectWaveformLevel(timeline.levels, { ...viewport, width })
    const centre = height / 2
    const visibleSeconds = Math.max(0.001, viewport.endSeconds - viewport.startSeconds)
    const firstBucket = Math.max(0, Math.floor(viewport.startSeconds / level.bucketDurationSeconds))
    const lastBucket = Math.min(level.maximum.length - 1, Math.ceil(viewport.endSeconds / level.bucketDurationSeconds))

    graphics.clear()
    const beats = timeline.beats ?? EMPTY_EVENTS
    for (let index = firstEventAtOrAfter(beats, viewport.startSeconds); index < beats.length; index += 1) {
      const beat = beats[index]!
      if (beat > viewport.endSeconds) break
      const x = ((beat - viewport.startSeconds) / visibleSeconds) * width
      graphics.moveTo(x, 0).lineTo(x, height)
        .stroke({ color: this.input.palette.beat ?? 0xffffff, width: 1, alpha: 0.2 })
    }
    for (let index = firstBucket; index <= lastBucket; index += 1) {
      const x = ((index * level.bucketDurationSeconds - viewport.startSeconds) / visibleSeconds) * width
      const top = centre - (level.maximum[index] / 32_767) * centre
      const bottom = centre - (level.minimum[index] / 32_767) * centre
      const colour = energyColour(this.input, level.low[index], level.mid[index], level.high[index])
      graphics.moveTo(x, top).lineTo(x, bottom).stroke({ color: colour, width: 1 })
    }
    const playheadX = ((playheadSeconds - viewport.startSeconds) / visibleSeconds) * width
    if (playheadX >= 0 && playheadX <= width) {
      graphics.moveTo(playheadX, 0).lineTo(playheadX, height)
        .stroke({ color: this.input.palette.playhead, width: 2 })
    }
    this.dirty = false
  }

  private readonly onTick = (): void => {
    if (this.dirty) this.draw()
  }
}
