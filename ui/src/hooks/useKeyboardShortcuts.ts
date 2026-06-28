import { useEffect } from "react"
import { usePlayerStore } from "@/store/playerStore"
import { audioEngine } from "@/engine/AudioEngine"

const SKIP_SECONDS = 10

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  const tag = el.tagName.toLowerCase()
  return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable
}

export function useKeyboardShortcuts() {
  const togglePlay = usePlayerStore((s) => s.togglePlay)
  const toggleMute = usePlayerStore((s) => s.toggleMute)
  const currentTrack = usePlayerStore((s) => s.currentTrack)
  const skipNext = usePlayerStore((s) => s.skipNext)

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (isTypingTarget(e.target)) return

      switch (e.code) {
        case "Space": {
          e.preventDefault()
          if (currentTrack) togglePlay()
          break
        }
        case "ArrowLeft": {
          if (currentTrack) {
            e.preventDefault()
            const t = Math.max(0, audioEngine.currentTime - SKIP_SECONDS)
            audioEngine.seekToSeconds(t)
          }
          break
        }
        case "ArrowRight": {
          if (currentTrack) {
            e.preventDefault()
            const dur = audioEngine.duration
            if (Number.isFinite(dur) && dur > 0) {
              const t = Math.min(dur, audioEngine.currentTime + SKIP_SECONDS)
              audioEngine.seekToSeconds(t)
            } else {
              skipNext()
            }
          }
          break
        }
        case "KeyM": {
          e.preventDefault()
          toggleMute()
          break
        }
      }
    }

    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [togglePlay, toggleMute, currentTrack, skipNext])
}
