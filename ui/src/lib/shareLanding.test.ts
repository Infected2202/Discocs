import { beforeEach, describe, expect, it, vi } from "vitest"
import { highlightTrackIdFromState, shareTargetPath, shareTargetState, stageSharedRelease } from "./shareLanding"

const createSession = vi.fn()
const patchSession = vi.fn()
const persistSessionId = vi.fn()

vi.mock("@/api/playback", () => ({
  createSession: (...args: unknown[]) => createSession(...args),
  patchSession: (...args: unknown[]) => patchSession(...args),
}))

vi.mock("@/store/sessionPersistence", () => ({
  persistSessionId: (...args: unknown[]) => persistSessionId(...args),
}))

function envelope(trackIds: number[]) {
  return {
    session: { id: "session-1" },
    queue: { items: trackIds.map((trackId, index) => ({ id: `item-${index}`, track_id: trackId })) },
  }
}

describe("shareLanding", () => {
  beforeEach(() => {
    createSession.mockReset()
    patchSession.mockReset()
    persistSessionId.mockReset()
    createSession.mockResolvedValue(envelope([10, 42, 11]))
    patchSession.mockResolvedValue(undefined)
  })

  it("lands on the release page of the share", () => {
    expect(shareTargetPath({ release_id: 7, release_title: "Album", track_id: 42 })).toBe("/releases/7")
  })

  it("asks the release page to frame the shared track, and only a shared track", () => {
    expect(shareTargetState({ release_id: 7, release_title: "Album", track_id: 42 })).toEqual({ highlightTrackId: 42 })
    expect(shareTargetState({ release_id: 7, release_title: "Album", track_id: null })).toBeUndefined()
  })

  it("reads the frame target back from a location state, and from nothing else", () => {
    expect(highlightTrackIdFromState({ highlightTrackId: 42 })).toBe(42)
    expect(highlightTrackIdFromState(null)).toBeNull()
    expect(highlightTrackIdFromState(undefined)).toBeNull()
    expect(highlightTrackIdFromState({})).toBeNull()
    expect(highlightTrackIdFromState({ highlightTrackId: "42" })).toBeNull()
    expect(highlightTrackIdFromState({ highlightTrackId: 4.5 })).toBeNull()
    expect(highlightTrackIdFromState("42")).toBeNull()
  })

  it("queues the whole release in order and points the player at the shared track", async () => {
    await stageSharedRelease({ release_id: 7, release_title: "Album", track_id: 42 })

    expect(createSession).toHaveBeenCalledWith(expect.objectContaining({
      source_type: "release",
      source_id: 7,
      source_label: "Album",
      mode: "linear",
      shuffle_enabled: false,
    }))
    expect(patchSession).toHaveBeenCalledWith("session-1", {
      current_track_id: 42,
      current_queue_item_id: "item-1",
    })
    expect(persistSessionId).toHaveBeenCalledWith("session-1")
  })

  it("points a whole-release share at its first track", async () => {
    await stageSharedRelease({ release_id: 7, release_title: "Album", track_id: null })

    expect(patchSession).toHaveBeenCalledWith("session-1", {
      current_track_id: 10,
      current_queue_item_id: "item-0",
    })
  })

  it("does not hand the shell a session whose pointer never moved", async () => {
    // Restoring it would greet the user with the release's first track.
    patchSession.mockRejectedValueOnce(new Error("offline"))

    await expect(stageSharedRelease({ release_id: 7, release_title: "Album", track_id: 42 })).rejects.toThrow("offline")
    expect(persistSessionId).not.toHaveBeenCalled()
  })
})
