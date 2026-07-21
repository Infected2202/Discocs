import type { Application, Graphics } from "pixi.js"
import { selectWaveformLevel } from "./geometry"
import type { WaveformRendererInput } from "./types"

export interface PixiModule {
  readonly Application: new () => Application
  readonly Graphics: new () => Graphics
}

export type PixiLoader = () => Promise<PixiModule>

type PixiCspInstaller = () => Promise<unknown>

const installPixiCspSupport: PixiCspInstaller = async () => import("pixi.js/unsafe-eval")
const importPixi: PixiLoader = async () => import("pixi.js")

export async function loadPixiWithCspSupport(
  installCspSupport: PixiCspInstaller = installPixiCspSupport,
  loader: PixiLoader = importPixi,
): Promise<PixiModule> {
  // Despite its package path, this module installs static implementations that
  // avoid eval when the application Content-Security-Policy forbids it.
  await installCspSupport()
  return loader()
}

const loadPixi: PixiLoader = loadPixiWithCspSupport
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
    if (events[middle] < seconds) low = middle + 1
    else high = middle
  }
  return low
}

export class PixiWaveformRenderer {
  private readonly container: HTMLElement
  private readonly loader: PixiLoader
  private input: WaveformRendererInput
  private app: Application | null = null
  private waveformGraphics: Graphics | null = null
  private historyGraphics: Graphics | null = null
  private beatGraphics: Graphics | null = null
  private playheadGraphics: Graphics | null = null
  private cancelled = false
  private resizeObserver: ResizeObserver | null = null

  constructor(container: HTMLElement, input: WaveformRendererInput, loader: PixiLoader = loadPixi) {
    this.container = container
    this.input = input
    this.loader = loader
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
      // Phone DPR is commonly 3-4, which multiplies the render target area by
      // 9-16 for no useful waveform detail. Coarse-pointer devices render at
      // CSS-pixel resolution; desktop is capped at 2x.
      resolution: Math.min(
        globalThis.matchMedia?.("(pointer: coarse)").matches ? 1 : 2,
        Math.max(1, this.input.viewport.devicePixelRatio),
      ),
      resizeTo: this.container,
      sharedTicker: false,
    })
    if (this.cancelled) {
      app.destroy(true, { children: true, texture: true, textureSource: true })
      return
    }

    this.app = app
    this.waveformGraphics = new Graphics()
    this.historyGraphics = new Graphics()
    this.beatGraphics = new Graphics()
    this.playheadGraphics = new Graphics()
    app.stage.addChild(this.waveformGraphics)
    app.stage.addChild(this.historyGraphics)
    app.stage.addChild(this.beatGraphics)
    app.stage.addChild(this.playheadGraphics)
    app.canvas.style.cssText = "display:block;width:100%;height:100%;touch-action:none"
    this.container.appendChild(app.canvas)

    this.resizeObserver = new ResizeObserver(() => {
      app.resize()
      this.draw()
    })
    this.resizeObserver.observe(this.container)
    this.draw()
  }

  update(input: WaveformRendererInput): void {
    if (sameInput(this.input, input)) return
    const redrawWaveform = waveformInputChanged(this.input, input)
    this.input = input
    this.draw(redrawWaveform)
  }

  destroy(): void {
    if (this.cancelled) return
    this.cancelled = true
    this.resizeObserver?.disconnect()
    this.resizeObserver = null
    if (!this.app) return
    this.app.stop()
    this.app.destroy(true, { children: true, texture: true, textureSource: true })
    this.app = null
    this.waveformGraphics = null
    this.historyGraphics = null
    this.beatGraphics = null
    this.playheadGraphics = null
  }

  private readonly draw = (redrawWaveform = true): void => {
    const waveformGraphics = this.waveformGraphics
    const historyGraphics = this.historyGraphics
    const beatGraphics = this.beatGraphics
    const playheadGraphics = this.playheadGraphics
    const app = this.app
    if (!waveformGraphics || !historyGraphics || !beatGraphics || !playheadGraphics || !app) return
    const { timeline, viewport, playheadSeconds } = this.input
    const width = Math.max(1, app.screen.width)
    const height = Math.max(1, app.screen.height)
    const level = selectWaveformLevel(timeline.levels, { ...viewport, width })
    const centre = height / 2
    const visibleSeconds = Math.max(0.001, viewport.endSeconds - viewport.startSeconds)
    const firstBucket = Math.max(0, Math.floor(viewport.startSeconds / level.bucketDurationSeconds))
    const lastBucket = Math.min(level.maximum.length - 1, Math.ceil(viewport.endSeconds / level.bucketDurationSeconds))

    if (redrawWaveform) {
      waveformGraphics.clear()
      for (let index = firstBucket; index <= lastBucket; index += 1) {
        const x = ((index * level.bucketDurationSeconds - viewport.startSeconds) / visibleSeconds) * width
        const top = centre - (level.maximum[index] / 32_767) * centre
        const bottom = centre - (level.minimum[index] / 32_767) * centre
        const colour = energyColour(this.input, level.low[index], level.mid[index], level.high[index])
        waveformGraphics.moveTo(x, top).lineTo(x, bottom).stroke({ color: colour, width: 1 })
      }

      beatGraphics.clear()
      if (this.input.follow) {
        const beats = timeline.beats ?? EMPTY_EVENTS
        for (let index = firstEventAtOrAfter(beats, viewport.startSeconds); index < beats.length; index += 1) {
          const beat = beats[index]
          if (beat > viewport.endSeconds) break
          const x = ((beat - viewport.startSeconds) / visibleSeconds) * width
          beatGraphics.moveTo(x, 0).lineTo(x, height)
            .stroke({ color: 0x05070a, width: 3, alpha: 0.62 })
          beatGraphics.moveTo(x, 0).lineTo(x, height)
            .stroke({ color: this.input.palette.beat ?? 0xffffff, width: 1, alpha: 0.55 })
        }
      }
    }

    const playheadX = ((playheadSeconds - viewport.startSeconds) / visibleSeconds) * width
    const historyWidth = Math.min(width, Math.max(0, playheadX))
    historyGraphics.clear()
    if (!this.input.follow && historyWidth > 0) {
      historyGraphics.rect(0, 0, historyWidth, height)
        .fill({ color: 0x030508, alpha: 0.42 })
    }

    playheadGraphics.clear()
    if (playheadX >= 0 && playheadX <= width) {
      playheadGraphics.moveTo(playheadX, 0).lineTo(playheadX, height)
        .stroke({ color: 0x05070a, width: 4, alpha: 0.72 })
      playheadGraphics.moveTo(playheadX, 0).lineTo(playheadX, height)
        .stroke({ color: this.input.palette.playhead, width: 2 })
    }
    // autoStart is disabled: render exactly one frame for an actual input or
    // size change instead of keeping four independent 60 FPS tickers alive.
    app.render()
  }
}

function waveformInputChanged(previous: WaveformRendererInput, next: WaveformRendererInput): boolean {
  return previous.timeline !== next.timeline
    || previous.viewport.startSeconds !== next.viewport.startSeconds
    || previous.viewport.endSeconds !== next.viewport.endSeconds
    || previous.palette !== next.palette
}

function sameInput(previous: WaveformRendererInput, next: WaveformRendererInput): boolean {
  const a = previous.viewport
  const b = next.viewport
  return previous.timeline === next.timeline
    && previous.playheadSeconds === next.playheadSeconds
    && previous.follow === next.follow
    && a.startSeconds === b.startSeconds
    && a.endSeconds === b.endSeconds
    && a.devicePixelRatio === b.devicePixelRatio
    && previous.palette === next.palette
}
