import { createSession, patchSession } from "@/api/playback"
import type { ShareMemberTarget } from "@/api/shares"
import { persistSessionId } from "@/store/sessionPersistence"

/** The library page a signed-in user lands on instead of the guest player. */
export function shareTargetPath(target: ShareMemberTarget): string {
  return `/releases/${target.release_id}`
}

/**
 * Put the shared release in the player, paused on the shared track (or the
 * first one for a whole-release share), so the user lands with one tap left:
 * play.
 *
 * Nothing touches the audio engine here. The share page lives outside
 * AppShell, and the shell restores the persisted session — paused — when it
 * mounts on the release page, exactly as after a reload. The pointer is moved
 * with a session PATCH rather than a queue jump, so no listening event is
 * recorded for a track nobody has started.
 */
export async function stageSharedRelease(target: ShareMemberTarget): Promise<void> {
  const envelope = await createSession({
    source_type: "release",
    source_id: target.release_id,
    source_label: target.release_title,
    mode: "linear",
    shuffle_enabled: false,
  })
  const items = envelope.queue.items
  const item = items.find((entry) => entry.track_id === target.track_id) ?? items[0]
  if (!item) return
  await patchSession(envelope.session.id, {
    current_track_id: item.track_id,
    current_queue_item_id: item.id,
  })
  persistSessionId(envelope.session.id)
}
