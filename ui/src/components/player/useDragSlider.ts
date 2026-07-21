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
 * Shared pointerdown → pointermove → pointerup drag tracking for
 * horizontal sliders. Pointer events cover mouse, touch and pen uniformly.
 */
export function useDragSlider({ onChange, onCommit }: UseDragSliderOptions) {
  const trackRef = useRef<HTMLDivElement>(null)

  const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
    const pointerId = e.pointerId
    let lastValue: number | null = null

    function valueFromClientX(clientX: number): number | null {
      const rect = trackRef.current?.getBoundingClientRect()
      if (!rect || rect.width <= 0) return null
      return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    }

    const initial = valueFromClientX(e.clientX)
    if (initial !== null) {
      lastValue = initial
      onChange(initial)
    }
    e.currentTarget.setPointerCapture?.(pointerId)

    function onMove(ev: PointerEvent) {
      if (ev.pointerId !== pointerId) return
      const v = valueFromClientX(ev.clientX)
      if (v !== null) {
        lastValue = v
        onChange(v)
      }
    }

    function finish(ev: PointerEvent) {
      if (ev.pointerId !== pointerId) return
      // Some mobile browsers report clientX=0 for pointerup after pointer
      // capture. Committing that release coordinate restarts playback even
      // though the drag preview was at the requested position. Pointermove is
      // the authoritative drag stream; a tap already populated lastValue from
      // pointerdown.
      if (lastValue !== null) onCommit?.(lastValue)
      cleanup()
    }

    function cancel(ev: PointerEvent) {
      if (ev.pointerId !== pointerId) return
      cleanup()
    }

    function cleanup() {
      globalThis.removeEventListener("pointermove", onMove)
      globalThis.removeEventListener("pointerup", finish)
      globalThis.removeEventListener("pointercancel", cancel)
    }

    globalThis.addEventListener("pointermove", onMove)
    globalThis.addEventListener("pointerup", finish)
    globalThis.addEventListener("pointercancel", cancel)
  }, [onChange, onCommit])

  return { trackRef, handlePointerDown }
}
