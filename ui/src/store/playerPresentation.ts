import { usePlayerStore } from "./playerStore"
import { useUIStore } from "./uiStore"

export function openDjPresentation(): void {
  usePlayerStore.setState({ expanded: false })
  useUIStore.getState().openDjSurface()
}
