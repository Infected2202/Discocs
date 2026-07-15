import { useCallback, useEffect, useRef, type RefObject } from "react"

const SNAP_RESTORE_DELAY_MS = 140
const RESTING_SNAP_TYPE = "x proximity"

export function useTouchMomentum(ref: RefObject<HTMLElement | null>) {
  const restoreTimerRef = useRef<number | null>(null)
  const momentumActiveRef = useRef(false)
  const touchingRef = useRef(false)

  const clearRestoreTimer = useCallback(() => {
    if (restoreTimerRef.current !== null) {
      window.clearTimeout(restoreTimerRef.current)
      restoreTimerRef.current = null
    }
  }, [])

  const restoreSnap = useCallback(() => {
    clearRestoreTimer()
    const element = ref.current
    momentumActiveRef.current = false
    touchingRef.current = false
    if (element) element.style.scrollSnapType = RESTING_SNAP_TYPE
  }, [clearRestoreTimer, ref])

  const scheduleSnapRestore = useCallback(() => {
    if (!momentumActiveRef.current || touchingRef.current) return
    clearRestoreTimer()
    restoreTimerRef.current = window.setTimeout(restoreSnap, SNAP_RESTORE_DELAY_MS)
  }, [clearRestoreTimer, restoreSnap])

  const onTouchStart = useCallback(() => {
    clearRestoreTimer()
    const element = ref.current
    if (!element) return

    momentumActiveRef.current = true
    touchingRef.current = true
    element.style.scrollSnapType = "none"
  }, [clearRestoreTimer, ref])

  const onTouchEnd = useCallback(() => {
    touchingRef.current = false
    scheduleSnapRestore()
  }, [scheduleSnapRestore])

  const onScroll = useCallback(() => {
    scheduleSnapRestore()
  }, [scheduleSnapRestore])

  useEffect(() => clearRestoreTimer, [clearRestoreTimer])

  return {
    onTouchStart,
    onTouchEnd,
    onTouchCancel: onTouchEnd,
    onScroll,
  }
}
