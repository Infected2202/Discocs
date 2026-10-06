import { createSession, patchSession } from "@/api/playback"
import type { ShareMemberTarget } from "@/api/shares"
import { persistSessionId } from "@/store/sessionPersistence"

/** The library page a signed-in user lands on instead of the guest player. */
export function shareTargetPath(target: ShareMemberTarget): string {
  return `/releases/${target.release_id}`
}

/**
 * What the release page needs to frame the shared track. It rides in the
 * history entry's state, not the URL: the address stays a plain, shareable
 * release link, and the glow is a one-off arrival cue rather than a permanent
 * property of the page.
 */
export interface ShareLandingState {
  highlightTrackId: number
}

export function shareTargetState(target: ShareMemberTarget): ShareLandingState | undefined {
  return target.track_id === null ? undefined : { highlightTrackId: target.track_id }
}

/** The track to frame, from a location's state — null for any other arrival. */
export function highlightTrackIdFromState(state: unknown): number | null {
  if (typeof state !== "object" || state === null) return null
  const id = (state as Partial<ShareLandingState>).highlightTrackId
  return typeof id === "number" && Number.isInteger(id) ? id : null
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
