import { playerPlayback } from "@/engine/playback"
import { usePlayerStore } from "./playerStore"
import { useUIStore } from "./uiStore"

export function openDjPresentation(): void {
  usePlayerStore.setState({ expanded: false })
  useUIStore.getState().openDjSurface()
  void playerPlayback.activateDjMode().catch((error: Error) => {
    usePlayerStore.setState({ error: error.message })
  })
}
