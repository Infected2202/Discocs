import { useEffect, useState } from "react"
import { loadTimeline, loadTimelineStatus, type TimelineLoadState } from "@/api/timeline"

const idle: TimelineLoadState = { status: "missing" }
const STATUS_POLL_MS = 2_000

async function resolveTimelineState(trackId: number, statusOnly: boolean): Promise<TimelineLoadState> {
  const loaded = statusOnly ? await loadTimelineStatus(trackId) : await loadTimeline(trackId)
  return statusOnly && loaded.status === "ready" ? loadTimeline(trackId) : loaded
}

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
    const refresh = async (statusOnly = false) => {
      const result = await resolveTimelineState(trackId, statusOnly)
      if (!current) return
      setState(result)
      if (result.status === "queued" || result.status === "running") {
        pollTimer = setTimeout(refreshStatus, STATUS_POLL_MS)
      }
    }
    const refreshStatus = () => {
      void refresh(true)
    }
    void refresh()
    return () => {
      current = false
      if (pollTimer !== undefined) clearTimeout(pollTimer)
    }
  }, [enabled, trackId])
  return state
}
