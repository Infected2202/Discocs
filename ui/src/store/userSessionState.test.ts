import { beforeEach, describe, expect, it, vi } from "vitest"

class MockAudio {
  src = ""
  preload = ""
  volume = 1
  muted = false
  currentTime = 0
  duration = Number.NaN
  paused = true
  ended = false
  error = null
  buffered = { length: 0, start: () => 0, end: () => 0 }

  addEventListener() {}
  removeEventListener() {}
  load = vi.fn()
  pause = vi.fn()
  play = vi.fn().mockResolvedValue(undefined)
}

describe("resetUserSessionState", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetModules()
    vi.stubGlobal("Audio", MockAudio)
  })

  it("removes every personal cache while preserving device preferences", async () => {
    const { resetUserSessionState } = await import("./userSessionState")
    const { queryClient } = await import("@/api/queryClient")
    const { usePlayerStore } = await import("./playerStore")
    const { useNavidromeStore } = await import("./navidromeStore")
    const { useUIStore } = await import("./uiStore")

    localStorage.setItem("discocs.sessionId.v1", "session-a")
    localStorage.setItem("discocs.playbackPosition.v1", JSON.stringify({
      sessionId: "session-a",
      queueItemId: "item-a",
      trackId: 7,
      seconds: 42,
    }))
    queryClient.setQueryData(["dashboard"], { owner: "alice" })
    usePlayerStore.setState({
      session: { id: "session-a" } as never,
      playedHistory: [{ id: "item-a" }] as never,
      volume: 0.4,
      muted: true,
    })
    useNavidromeStore.setState({
      likedIds: new Set([7]),
      likedAlbumIds: new Set([8]),
      likedArtistIds: new Set([9]),
    })
    useUIStore.setState({
      sidebarCollapsed: true,
      addToPlaylistTrackIds: [7],
      createPlaylistOptions: { defaultTitle: "Alice" },
    })

    resetUserSessionState()

    expect(localStorage.getItem("discocs.sessionId.v1")).toBeNull()
    expect(localStorage.getItem("discocs.playbackPosition.v1")).toBeNull()
    expect(queryClient.getQueryData(["dashboard"])).toBeUndefined()
    expect(usePlayerStore.getState()).toMatchObject({
      session: null,
      playedHistory: [],
      volume: 0.4,
      muted: true,
    })
    expect(useNavidromeStore.getState().likedIds.size).toBe(0)
    expect(useNavidromeStore.getState().likedAlbumIds.size).toBe(0)
    expect(useNavidromeStore.getState().likedArtistIds.size).toBe(0)
    expect(useUIStore.getState()).toMatchObject({
      sidebarCollapsed: true,
      addToPlaylistTrackIds: null,
      createPlaylistOptions: null,
    })
  })
})
