import { useCallback, useRef } from "react"

interface UseDragSliderOptions {
  /**
   * Called on mousedown and on every mousemove with the pointer's fraction
   * (0-1) along the track. Safe to write straight to real state when that
   * state is synchronous and fully store-driven (e.g. volume). For state
   * that's only updated asynchronously elsewhere (e.g. `currentTime`, which
   * only advances on the audio element's throttled `timeupdate` event),
   * write to a local draft instead and commit via `onCommit`.
   */
  onChange: (value: number) => void
  /** Called once when the drag ends (mouseup), with the final fraction. */
  onCommit?: (value: number) => void
}

/**
 * Shared mousedown → mousemove → mouseup drag tracking for horizontal
 * sliders (seek bar, volume bar), used by both PlayerBar and ExpandedPlayer.
 */
export function useDragSlider({ onChange, onCommit }: UseDragSliderOptions) {
  const trackRef = useRef<HTMLDivElement>(null)

  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()

    function valueFromClientX(clientX: number): number | null {
      const rect = trackRef.current?.getBoundingClientRect()
      if (!rect) return null
      return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    }

    const initial = valueFromClientX(e.clientX)
    if (initial !== null) onChange(initial)

    function onMove(ev: MouseEvent) {
      const v = valueFromClientX(ev.clientX)
      if (v !== null) onChange(v)
    }

    function onUp(ev: MouseEvent) {
      const v = valueFromClientX(ev.clientX)
      if (v !== null) onCommit?.(v)
      globalThis.removeEventListener("mousemove", onMove)
      globalThis.removeEventListener("mouseup", onUp)
    }

    globalThis.addEventListener("mousemove", onMove)
    globalThis.addEventListener("mouseup", onUp)
  }, [onChange, onCommit])

  return { trackRef, handleMouseDown }
}
