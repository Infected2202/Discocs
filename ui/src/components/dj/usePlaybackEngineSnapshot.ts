import { useEffect, useState } from "react"
import { playerPlayback } from "@/engine/playback"

export function usePlaybackEngineSnapshot() {
  const [snapshot, setSnapshot] = useState(() => playerPlayback.getEngineSnapshot())

  useEffect(() => playerPlayback.subscribeEngine(() => {
    setSnapshot(playerPlayback.getEngineSnapshot())
  }), [])

  return snapshot
}
