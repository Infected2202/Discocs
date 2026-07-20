export interface WaveformLevel {
  readonly bucketDurationSeconds: number
  readonly minimum: Int16Array
  readonly maximum: Int16Array
  readonly low: Uint16Array
  readonly mid: Uint16Array
  readonly high: Uint16Array
}

export interface WaveformTimeline {
  readonly durationSeconds: number
  readonly levels: readonly WaveformLevel[]
  readonly beats?: Float32Array
}

export interface WaveformViewport {
  readonly width: number
  readonly height: number
  readonly devicePixelRatio: number
  readonly startSeconds: number
  readonly endSeconds: number
}

export interface WaveformPalette {
  readonly low: number
  readonly mid: number
  readonly high: number
  readonly playhead: number
  readonly beat?: number
}

export interface WaveformRendererInput {
  readonly timeline: WaveformTimeline
  readonly viewport: WaveformViewport
  readonly playheadSeconds: number
  readonly follow: boolean
  readonly palette: WaveformPalette
}
