import { useEffect, useLayoutEffect, useRef, type RefObject } from "react"
import { useLocation, useNavigationType } from "react-router"

// Positions of the history entries we have left, by react-router location key.
// Module-level so they outlive AppShell re-renders; memory only — a reload
// starts from the top, which is what a reload normally does here.
const positions = new Map<string, number>()

const RESTORE_TIMEOUT_MS = 2000

// Pages render their lists asynchronously, so right after "back" the content is
// still too short to scroll to the saved offset. Retry every frame until it is
// tall enough, the user takes over the wheel/finger, or the timeout passes.
// Returns a cancel function.
export function restoreScroll(el: HTMLElement, target: number, onDone: () => void): () => void {
  const startedAt = performance.now()
  let frame = 0
  let cancelled = false

  const cancel = () => {
    if (cancelled) return
    cancelled = true
    cancelAnimationFrame(frame)
    el.removeEventListener("wheel", cancel)
    el.removeEventListener("touchmove", cancel)
    onDone()
  }

  const tick = () => {
    if (cancelled) return
    const reachable = el.scrollHeight - el.clientHeight
    el.scrollTop = Math.min(target, Math.max(0, reachable))
    if (reachable >= target || performance.now() - startedAt >= RESTORE_TIMEOUT_MS) {
      cancel()
      return
    }
    frame = requestAnimationFrame(tick)
  }

  el.addEventListener("wheel", cancel, { passive: true })
  el.addEventListener("touchmove", cancel, { passive: true })
  tick()
  return cancel
}

/**
 * The app scrolls inside <main>, not the window, so the browser's own scroll
 * restoration and react-router's <ScrollRestoration> never see it. This keeps
 * the offset per history entry: back/forward returns to where the user was,
 * a new page (PUSH or REPLACE to another path) starts from the top, while
 * a same-path navigation (search-param updates, the expanded player's history
 * entry) leaves the scroll alone.
 */
export function useScrollRestoration(ref: RefObject<HTMLElement | null>) {
  const location = useLocation()
  const navigationType = useNavigationType()

  const currentKey = useRef(location.key)
  const currentPath = useRef(location.pathname)
  // Last offset the user actually scrolled to. Kept from scroll events rather
  // than read on navigation: by then the new page has already replaced the
  // content and the browser may have clamped scrollTop.
  const lastTop = useRef(0)
  const restoring = useRef(false)
  const cancelRestore = useRef<(() => void) | null>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onScroll = () => {
      if (!restoring.current) lastTop.current = el.scrollTop
    }
    el.addEventListener("scroll", onScroll, { passive: true })
    return () => el.removeEventListener("scroll", onScroll)
  }, [ref])

  useLayoutEffect(() => {
    if (location.key === currentKey.current) return
    const el = ref.current
    const previousKey = currentKey.current
    const pathChanged = location.pathname !== currentPath.current
    currentKey.current = location.key
    currentPath.current = location.pathname
    if (!el) return

    cancelRestore.current?.()
    positions.set(previousKey, lastTop.current)

    if (navigationType === "POP") {
      const target = positions.get(location.key)
      if (target === undefined) return
      restoring.current = true
      lastTop.current = target
      cancelRestore.current = restoreScroll(el, target, () => {
        restoring.current = false
        lastTop.current = el.scrollTop
      })
    } else if (pathChanged) {
      el.scrollTop = 0
      lastTop.current = 0
    }
  }, [location.key, location.pathname, navigationType, ref])
}

export function resetScrollPositions() {
  positions.clear()
}
