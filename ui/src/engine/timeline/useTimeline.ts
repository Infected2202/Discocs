import { useEffect, useState } from "react"
import { loadTimeline, type TimelineLoadState } from "@/api/timeline"

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
    const refresh = () => {
      void loadTimeline(trackId).then((result) => {
        if (!current) return
        setState(result)
        if (result.status === "queued" || result.status === "running") {
          pollTimer = setTimeout(refresh, STATUS_POLL_MS)
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
