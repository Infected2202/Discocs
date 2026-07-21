import { useEffect, useState } from "react"
import { loadTimeline, loadTimelineStatus, type TimelineLoadState } from "@/api/timeline"

const idle: TimelineLoadState = { status: "missing" }
const STATUS_POLL_MS = 2_000

export function useTimeline(trackId: number | null, enabled = true): TimelineLoadState {
  const [state, setState] = useState<TimelineLoadState>(idle)
  useEffect(() => {
    let current = true
    let pollTimer: ReturnType<typeof setTimeout> | undefined
    if (trackId === null || !enabled) {
      setState(idle)
      return () => { current = false }
    }
    setState({ status: "loading" })
    const refresh = (statusOnly = false) => {
      const pending = statusOnly ? loadTimelineStatus(trackId) : loadTimeline(trackId)
      void pending.then(async (loaded) => {
        if (!current) return
        const result = statusOnly && loaded.status === "ready"
          ? await loadTimeline(trackId)
          : loaded
        if (!current) return
        setState(result)
        if (result.status === "queued" || result.status === "running") {
          pollTimer = setTimeout(() => refresh(true), STATUS_POLL_MS)
        }
      })
    }
    refresh()
    return () => {
      current = false
      if (pollTimer !== undefined) clearTimeout(pollTimer)
    }
  }, [enabled, trackId])
  return state
}
