import { useEffect, useRef } from "react"
import { useLocation, useNavigate, useNavigationType } from "react-router"
import { usePlayerStore } from "@/store/playerStore"

// Flag carried in `location.state` of the history entry the expanded player
// pushes. The URL stays the same; only the entry is new.
const PLAYER_STATE_KEY = "playerOpen"

function isPlayerEntry(state: unknown): boolean {
  return typeof state === "object" && state !== null && (state as Record<string, unknown>)[PLAYER_STATE_KEY] === true
}

/**
 * Gives the expanded player its own history entry, so the browser/system
 * "back" (and the mobile back swipe) collapses the player first and only then
 * walks back through pages.
 *
 * `playerStore.expanded` stays the source of truth for rendering; this hook
 * keeps the two in step by watching *transitions* (not states), which keeps
 * opening, closing and navigating away from racing each other:
 *  - expanded false→true  : push a same-URL entry flagged `playerOpen`
 *  - expanded true→false  : if that entry is still current, go back one step
 *  - flagged entry left (back, or a link that replaces it) while expanded
 *                         : collapse the player
 *  - flagged entry reached by "forward" while collapsed : expand again
 *  - flagged entry present on first load (reload while expanded): do not
 *    reopen; step back off it so history has no phantom entry
 */
export function usePlayerHistory() {
  const location = useLocation()
  const navigationType = useNavigationType()
  const navigate = useNavigate()
  const expanded = usePlayerStore((s) => s.expanded)

  const onPlayerEntry = isPlayerEntry(location.state)
  const wasOnPlayerEntry = useRef(onPlayerEntry)
  const wasExpanded = useRef(expanded)
  const droppedStaleEntry = useRef(false)

  // Reload while expanded: the flagged entry survives in history, the store does not.
  useEffect(() => {
    if (!onPlayerEntry || usePlayerStore.getState().expanded || droppedStaleEntry.current) return
    droppedStaleEntry.current = true
    void navigate(-1)
    // Only the initial location matters here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Store → history.
  useEffect(() => {
    if (expanded === wasExpanded.current) return
    wasExpanded.current = expanded
    if (expanded && !onPlayerEntry) {
      void navigate(
        { pathname: location.pathname, search: location.search, hash: location.hash },
        { state: { ...(location.state as object | null), [PLAYER_STATE_KEY]: true } }
      )
    } else if (!expanded && onPlayerEntry) {
      void navigate(-1)
    }
  }, [expanded, onPlayerEntry, location, navigate])

  // History → store.
  useEffect(() => {
    if (onPlayerEntry === wasOnPlayerEntry.current) return
    wasOnPlayerEntry.current = onPlayerEntry
    const store = usePlayerStore.getState()
    if (!onPlayerEntry && store.expanded) {
      wasExpanded.current = false
      usePlayerStore.setState({ expanded: false })
    } else if (onPlayerEntry && !store.expanded && navigationType === "POP") {
      wasExpanded.current = true
      usePlayerStore.setState({ expanded: true })
    }
  }, [onPlayerEntry, navigationType])
}
