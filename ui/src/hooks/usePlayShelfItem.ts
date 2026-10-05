import { apiFetch } from "@/api/client"
import type { PlaybackEnvelope, ShelfItem } from "@/api/types"
import { usePlayerStore } from "@/store/playerStore"

/**
 * Play handler of a backend shelf item: a `post` play action starts the
 * session the server builds, otherwise the source is played directly.
 * Failures are ignored like on the dashboard shelves.
 */
export function usePlayShelfItem(): (item: ShelfItem) => void {
  const playSource = usePlayerStore((s) => s.playSource)
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)

  return (item: ShelfItem) => {
    const action = item.play_action
    if (!action) return
    if (action.type === "post") {
      apiFetch<PlaybackEnvelope>(action.endpoint, { method: "POST" })
        .then((envelope) => playFromEnvelope(envelope))
        .catch(() => {})
    } else {
      playSource(action.source_type, Number(action.source_id), action.source_label ?? item.title)
    }
  }
}
