import { useEffect } from "react"
import { usePlayerStore } from "@/store/playerStore"

const BASE_TITLE = "discocs"

export function useTrackTitle() {
  const currentTrack = usePlayerStore((s) => s.currentTrack)
  const playbackState = usePlayerStore((s) => s.playbackState)

  useEffect(() => {
    if (currentTrack && playbackState !== "idle") {
      const artists = currentTrack.artists?.map((a) => a.name).join(", ") ?? ""
      document.title = artists
        ? `${currentTrack.title} · ${artists} — ${BASE_TITLE}`
        : `${currentTrack.title} — ${BASE_TITLE}`
    } else {
      document.title = BASE_TITLE
    }
  }, [currentTrack, playbackState])
}
