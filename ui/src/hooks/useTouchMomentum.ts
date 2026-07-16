import { useCallback, useEffect, useRef, type RefObject } from "react"

const SNAP_RESTORE_DELAY_MS = 140
const ALIGNMENT_SETTLE_DELAY_MS = 100
const ALIGNMENT_FALLBACK_MS = 350
const RESTING_SNAP_TYPE = "x proximity"

function nearestSnapTarget(element: HTMLElement): number {
  const items = Array.from(element.children).filter(
    (child): child is HTMLElement => child instanceof HTMLElement
  )
  if (items.length === 0) return element.scrollLeft

  const origin = items[0].offsetLeft
  const maxScrollLeft = Math.max(0, element.scrollWidth - element.clientWidth)
  return items.reduce((nearest, item) => {
    const target = Math.min(maxScrollLeft, Math.max(0, item.offsetLeft - origin))
    return Math.abs(target - element.scrollLeft) < Math.abs(nearest - element.scrollLeft)
      ? target
      : nearest
  }, 0)
}

export function useTouchMomentum(ref: RefObject<HTMLElement | null>) {
  const restoreTimerRef = useRef<number | null>(null)
  const momentumActiveRef = useRef(false)
  const touchingRef = useRef(false)
  const aligningRef = useRef(false)

  const clearRestoreTimer = useCallback(() => {
    if (restoreTimerRef.current !== null) {
      window.clearTimeout(restoreTimerRef.current)
      restoreTimerRef.current = null
    }
  }, [])

  const finishAlignment = useCallback(() => {
    clearRestoreTimer()
    const element = ref.current
    momentumActiveRef.current = false
    touchingRef.current = false
    aligningRef.current = false
    if (element) element.style.scrollSnapType = RESTING_SNAP_TYPE
  }, [clearRestoreTimer, ref])

  const beginAlignment = useCallback(() => {
    clearRestoreTimer()
    const element = ref.current
    momentumActiveRef.current = false
    if (!element) return

    const target = nearestSnapTarget(element)
    if (Math.abs(target - element.scrollLeft) < 1) {
      finishAlignment()
      return
    }

    aligningRef.current = true
    element.scrollTo({ left: target, behavior: "smooth" })
    restoreTimerRef.current = window.setTimeout(finishAlignment, ALIGNMENT_FALLBACK_MS)
  }, [clearRestoreTimer, finishAlignment, ref])

  const scheduleAlignment = useCallback(() => {
    if (!momentumActiveRef.current || touchingRef.current) return
    clearRestoreTimer()
    restoreTimerRef.current = window.setTimeout(beginAlignment, SNAP_RESTORE_DELAY_MS)
  }, [beginAlignment, clearRestoreTimer])

  const onTouchStart = useCallback(() => {
    clearRestoreTimer()
    const element = ref.current
    if (!element) return

    momentumActiveRef.current = true
    touchingRef.current = true
    aligningRef.current = false
    element.style.scrollSnapType = "none"
  }, [clearRestoreTimer, ref])

  const onTouchEnd = useCallback(() => {
    touchingRef.current = false
    scheduleAlignment()
  }, [scheduleAlignment])

  const onScroll = useCallback(() => {
    if (touchingRef.current) return
    if (aligningRef.current) {
      clearRestoreTimer()
      restoreTimerRef.current = window.setTimeout(finishAlignment, ALIGNMENT_SETTLE_DELAY_MS)
      return
    }
    scheduleAlignment()
  }, [clearRestoreTimer, finishAlignment, scheduleAlignment])

  useEffect(() => clearRestoreTimer, [clearRestoreTimer])

  return {
    onTouchStart,
    onTouchEnd,
    onTouchCancel: onTouchEnd,
    onScroll,
  }
}
