import { useEffect, useRef } from "react"
import { pointerXToTime, tapeDragTime } from "./geometry"
import { PixiWaveformRenderer } from "./PixiWaveformRenderer"
import type { WaveformRendererInput } from "./types"

export interface WaveformSurfaceProps {
  readonly input: WaveformRendererInput
  readonly className?: string
  readonly ariaLabel?: string
  readonly onSeek?: (seconds: number) => void
  readonly interaction?: "absolute" | "tape"
}

interface WaveformDrag {
  readonly pointerId: number
  readonly startX: number
  readonly startSeconds: number
  moved: boolean
  lastSeconds: number
}

export function WaveformSurface({ input, className, ariaLabel, onSeek, interaction = "absolute" }: WaveformSurfaceProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const rendererRef = useRef<PixiWaveformRenderer | null>(null)
  const dragRef = useRef<WaveformDrag | null>(null)

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

  function absoluteTime(element: HTMLDivElement, clientX: number): number {
    const rect = element.getBoundingClientRect()
    const seconds = pointerXToTime(clientX - rect.left, { ...input.viewport, width: rect.width })
    return Math.min(Math.max(0, seconds), input.timeline.durationSeconds)
  }

  return (
    <div
      ref={containerRef}
      className={className}
      aria-label={ariaLabel}
      onPointerDown={(event) => {
        if (!onSeek) return
        event.preventDefault()
        event.currentTarget.setPointerCapture?.(event.pointerId)
        const initial = absoluteTime(event.currentTarget, event.clientX)
        dragRef.current = {
          pointerId: event.pointerId,
          startX: event.clientX,
          startSeconds: input.playheadSeconds,
          moved: false,
          lastSeconds: initial,
        }
        if (interaction === "absolute") onSeek(initial)
      }}
      onPointerMove={(event) => {
        const drag = dragRef.current
        if (!drag || drag.pointerId !== event.pointerId || !onSeek) return
        const delta = event.clientX - drag.startX
        if (Math.abs(delta) >= 2) drag.moved = true
        if (!drag.moved && interaction === "tape") return
        const rect = event.currentTarget.getBoundingClientRect()
        const next = interaction === "tape"
          ? tapeDragTime(
              drag.startSeconds,
              delta,
              { ...input.viewport, width: rect.width },
              input.timeline.durationSeconds,
            )
          : absoluteTime(event.currentTarget, event.clientX)
        drag.lastSeconds = next
        onSeek(next)
      }}
      onPointerUp={(event) => {
        const drag = dragRef.current
        if (!drag || drag.pointerId !== event.pointerId || !onSeek) return
        if (interaction === "tape" && !drag.moved) {
          onSeek(drag.lastSeconds)
        }
        dragRef.current = null
      }}
      onPointerCancel={(event) => {
        if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null
      }}
    />
  )
}
