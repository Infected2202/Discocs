import { describe, it, expect, beforeEach } from "vitest"
import {
  persistPlaybackPosition,
  loadPersistedPlaybackPosition,
  clearPersistedPlaybackPosition,
  playbackPositionMatches,
  persistSessionId,
  loadPersistedSessionId,
  clearPersistedSessionId,
} from "./sessionPersistence"

describe("session id persistence", () => {
  beforeEach(() => localStorage.clear())

  it("round-trips the session id", () => {
    persistSessionId("sess-42")
    expect(loadPersistedSessionId()).toBe("sess-42")
    clearPersistedSessionId()
    expect(loadPersistedSessionId()).toBeNull()
  })
})

describe("playback position persistence", () => {
  beforeEach(() => localStorage.clear())

  it("round-trips track id and position", () => {
    persistPlaybackPosition({ sessionId: "session-1", queueItemId: "item-3", trackId: 7, seconds: 123.5 })
    expect(loadPersistedPlaybackPosition()).toEqual({
      sessionId: "session-1",
      queueItemId: "item-3",
      trackId: 7,
      seconds: 123.5,
    })
  })

  it("returns null when nothing was persisted", () => {
    expect(loadPersistedPlaybackPosition()).toBeNull()
  })

  it("returns null for corrupted or invalid payloads", () => {
    localStorage.setItem("discocs.playbackPosition.v1", "not-json")
    expect(loadPersistedPlaybackPosition()).toBeNull()

    localStorage.setItem("discocs.playbackPosition.v1", JSON.stringify({ sessionId: "s", queueItemId: "q", trackId: "7", seconds: 10 }))
    expect(loadPersistedPlaybackPosition()).toBeNull()

    localStorage.setItem("discocs.playbackPosition.v1", JSON.stringify({ sessionId: "s", queueItemId: "q", trackId: 7, seconds: -5 }))
    expect(loadPersistedPlaybackPosition()).toBeNull()

    // Legacy values without session/queue identity must not leak into a new occurrence.
    localStorage.setItem("discocs.playbackPosition.v1", JSON.stringify({ trackId: 7, seconds: 10 }))
    expect(loadPersistedPlaybackPosition()).toBeNull()
  })

  it("matches only the same session, queue item, and track", () => {
    const persisted = { sessionId: "session-1", queueItemId: "item-3", trackId: 7, seconds: 12 }
    expect(playbackPositionMatches(persisted, persisted)).toBe(true)
    expect(playbackPositionMatches(persisted, { ...persisted, sessionId: "session-2" })).toBe(false)
    expect(playbackPositionMatches(persisted, { ...persisted, queueItemId: "item-4" })).toBe(false)
    expect(playbackPositionMatches(persisted, { ...persisted, trackId: 8 })).toBe(false)
  })

  it("clears the persisted position", () => {
    persistPlaybackPosition({ sessionId: "session-1", queueItemId: "item-3", trackId: 7, seconds: 10 })
    clearPersistedPlaybackPosition()
    expect(loadPersistedPlaybackPosition()).toBeNull()
  })
})
