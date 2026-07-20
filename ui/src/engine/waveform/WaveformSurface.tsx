import { useEffect, useRef } from "react"
import { pointerXToTime } from "./geometry"
import { PixiWaveformRenderer } from "./PixiWaveformRenderer"
import type { WaveformRendererInput } from "./types"

export interface WaveformSurfaceProps {
  readonly input: WaveformRendererInput
  readonly className?: string
  readonly onSeek?: (seconds: number) => void
}

export function WaveformSurface({ input, className, onSeek }: WaveformSurfaceProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const rendererRef = useRef<PixiWaveformRenderer | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const renderer = new PixiWaveformRenderer(container, input)
    rendererRef.current = renderer
    void renderer.mount()
    return () => {
      renderer.destroy()
      rendererRef.current = null
    }
  }, [])

  useEffect(() => rendererRef.current?.update(input), [input])

  return (
    <div
      ref={containerRef}
      className={className}
      onPointerDown={(event) => {
        if (!onSeek) return
        const rect = event.currentTarget.getBoundingClientRect()
        onSeek(pointerXToTime(event.clientX - rect.left, { ...input.viewport, width: rect.width }))
      }}
    />
  )
}
