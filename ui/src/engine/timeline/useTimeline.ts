import { useEffect, useState } from "react"
import { loadTimeline, type TimelineLoadState } from "@/api/timeline"

const idle: TimelineLoadState = { status: "missing" }

export function useTimeline(trackId: number | null, enabled = true): TimelineLoadState {
  const [state, setState] = useState<TimelineLoadState>(idle)
  useEffect(() => {
    let current = true
    if (trackId === null || !enabled) {
      setState(idle)
      return () => { current = false }
    }
    setState({ status: "loading" })
    void loadTimeline(trackId).then((result) => { if (current) setState(result) })
    return () => { current = false }
  }, [enabled, trackId])
  return state
}
