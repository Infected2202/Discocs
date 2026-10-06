import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const apiFetch = vi.fn()

vi.mock("./client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  apiUrl: (path: string, params?: Record<string, string | number | boolean | undefined>) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(params ?? {})) {
      if (value !== undefined) search.set(key, String(value))
    }
    const query = search.toString()
    return query ? `${path}?${query}` : path
  },
}))

import {
  fetchUserLikes,
  fetchUserLikesOfKind,
  fetchUserListens,
  fetchUserPlaylists,
  fetchUserProfile,
  fetchUserTop,
  fetchUserTopTracks,
  isProfilePeriod,
  setMyAvatar,
  viewerTimeZone,
} from "./profile"

describe("profile API", () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiFetch.mockResolvedValue({})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("requests the profile for a period in the viewer's time zone", async () => {
    await fetchUserProfile("alice", "90d", "Europe/Moscow")
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/users/alice/profile?period=90d&tz=Europe%2FMoscow")
  })

  it("escapes the username in the path", async () => {
    await fetchUserPlaylists("a b/c")
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/users/a%20b%2Fc/playlists")
  })

  it("pages the listening history by offset", async () => {
    await fetchUserListens("bob", { limit: 50, offset: 100 })
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/users/bob/listens?limit=50&offset=100")
  })

  it("asks for likes with a limit", async () => {
    await fetchUserLikes("bob", { limit: 24 })
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/users/bob/likes?limit=24")
  })

  it("pages a period's top of one kind", async () => {
    await fetchUserTop("bob", "releases", { period: "7d", tz: "UTC", limit: 48, offset: 96 })
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/users/bob/top/releases?period=7d&tz=UTC&limit=48&offset=96")
  })

  it("pages a period's top tracks", async () => {
    await fetchUserTopTracks("bob", { period: "90d", tz: "UTC", limit: 50, offset: 50 })
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/users/bob/top/tracks?period=90d&tz=UTC&limit=50&offset=50")
  })

  it("pages one kind of likes and the playlists", async () => {
    await fetchUserLikesOfKind("bob", "artists", { limit: 48, offset: 0 })
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/users/bob/likes/artists?limit=48&offset=0")
    await fetchUserPlaylists("bob", { limit: 16 })
    expect(apiFetch).toHaveBeenLastCalledWith("/api/v1/users/bob/playlists?limit=16")
  })

  it("accepts only known periods from the URL", () => {
    expect(isProfilePeriod("365d")).toBe(true)
    expect(isProfilePeriod("14d")).toBe(false)
    expect(isProfilePeriod(null)).toBe(false)
  })

  it("saves the avatar with PUT /me/avatar {key}", async () => {
    await setMyAvatar("a04")
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/me/avatar", {
      method: "PUT",
      body: JSON.stringify({ key: "a04" }),
    })
  })

  it("reads the IANA zone from Intl", () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      timeZone: "Asia/Tokyo",
    } as Intl.ResolvedDateTimeFormatOptions)
    expect(viewerTimeZone()).toBe("Asia/Tokyo")
  })
})
