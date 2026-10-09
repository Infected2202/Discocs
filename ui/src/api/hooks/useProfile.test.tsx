import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { PEOPLE_QUERY_KEY } from "./usePeople"
import {
  PROFILE_QUERY_KEY,
  useRefreshListensOnPlayChange,
  useSetMyAvatar,
  useUserAlbumsForYou,
  useUserAlbumsForYouList,
  useUserLikes,
  useUserLikesList,
  useUserListens,
  useUserMixes,
  useUserMixesList,
  useUserPlaylists,
  useUserProfile,
  useUserTopList,
  useUserTopTracksList,
} from "./useProfile"
import type { ProfilePeriod } from "../profile"
import type { PersonNowPlaying } from "../social"

const fetchUserProfile = vi.fn()
const fetchUserListens = vi.fn()
const fetchUserTop = vi.fn()
const fetchUserTopTracks = vi.fn()
const fetchUserLikes = vi.fn()
const fetchUserLikesOfKind = vi.fn()
const fetchUserPlaylists = vi.fn()
const fetchUserMixes = vi.fn()
const fetchUserAlbumsForYou = vi.fn()
const setMyAvatar = vi.fn()

vi.mock("../profile", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../profile")>()
  return {
    ...actual,
    fetchUserProfile: (...args: unknown[]) => fetchUserProfile(...args),
    fetchUserListens: (...args: unknown[]) => fetchUserListens(...args),
    fetchUserTop: (...args: unknown[]) => fetchUserTop(...args),
    fetchUserTopTracks: (...args: unknown[]) => fetchUserTopTracks(...args),
    fetchUserLikes: (...args: unknown[]) => fetchUserLikes(...args),
    fetchUserLikesOfKind: (...args: unknown[]) => fetchUserLikesOfKind(...args),
    fetchUserPlaylists: (...args: unknown[]) => fetchUserPlaylists(...args),
    fetchUserMixes: (...args: unknown[]) => fetchUserMixes(...args),
    fetchUserAlbumsForYou: (...args: unknown[]) => fetchUserAlbumsForYou(...args),
    setMyAvatar: (...args: unknown[]) => setMyAvatar(...args),
    viewerTimeZone: () => "Europe/Moscow",
  }
})

let queryClient: QueryClient

function wrapper({ children }: { readonly children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

describe("useUserProfile", () => {
  beforeEach(() => {
    fetchUserProfile.mockReset()
    fetchUserProfile.mockImplementation((_user: string, period: string) => Promise.resolve({ period: { key: period } }))
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  })

  it("passes the viewer's time zone and refetches when the period changes", async () => {
    const { result, rerender } = renderHook(
      ({ period }: { period: ProfilePeriod }) => useUserProfile("alice", period),
      { wrapper, initialProps: { period: "30d" } },
    )
    await waitFor(() => expect(result.current.data).toEqual({ period: { key: "30d" } }))
    expect(fetchUserProfile).toHaveBeenLastCalledWith("alice", "30d", "Europe/Moscow")

    rerender({ period: "7d" })

    await waitFor(() => expect(result.current.data).toEqual({ period: { key: "7d" } }))
    expect(fetchUserProfile).toHaveBeenLastCalledWith("alice", "7d", "Europe/Moscow")
    expect(fetchUserProfile).toHaveBeenCalledTimes(2)
  })
})

describe("useUserListens", () => {
  beforeEach(() => {
    fetchUserListens.mockReset()
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  })

  it("follows next_offset page by page and stops on the last page", async () => {
    fetchUserListens
      .mockResolvedValueOnce({ items: [], total: 70, limit: 50, offset: 0, next_offset: 50 })
      .mockResolvedValueOnce({ items: [], total: 70, limit: 50, offset: 50, next_offset: null })
    const { result } = renderHook(() => useUserListens("bob"), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.hasNextPage).toBe(true)
    await act(async () => {
      await result.current.fetchNextPage()
    })

    expect(fetchUserListens).toHaveBeenNthCalledWith(1, "bob", { limit: 50, offset: 0 })
    expect(fetchUserListens).toHaveBeenNthCalledWith(2, "bob", { limit: 50, offset: 50 })
    await waitFor(() => expect(result.current.hasNextPage).toBe(false))
  })
})

describe("profile shelves", () => {
  beforeEach(() => {
    fetchUserLikes.mockReset().mockResolvedValue({})
    fetchUserPlaylists.mockReset().mockResolvedValue({})
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  })

  it("preview likes and playlists with the common shelf size", async () => {
    renderHook(() => useUserLikes("bob"), { wrapper })
    renderHook(() => useUserPlaylists("bob"), { wrapper })

    await waitFor(() => expect(fetchUserPlaylists).toHaveBeenCalled())
    expect(fetchUserLikes).toHaveBeenCalledWith("bob", { limit: 16 })
    expect(fetchUserPlaylists).toHaveBeenCalledWith("bob", { limit: 16 })
  })
})

describe("full lists of profile shelves", () => {
  beforeEach(() => {
    fetchUserTop.mockReset()
    fetchUserTopTracks.mockReset()
    fetchUserLikesOfKind.mockReset()
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  })

  it("pages the period's top in the viewer's time zone", async () => {
    fetchUserTop
      .mockResolvedValueOnce({ items: [], total: 60, next_offset: 48 })
      .mockResolvedValueOnce({ items: [], total: 60, next_offset: null })
    const { result } = renderHook(() => useUserTopList("bob", "artists", "90d"), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    // Reading hasNextPage before paging also makes React Query track it
    // (tracked result props): otherwise its change wouldn't re-render the hook.
    expect(result.current.hasNextPage).toBe(true)
    await act(async () => {
      await result.current.fetchNextPage()
    })

    expect(fetchUserTop).toHaveBeenNthCalledWith(1, "bob", "artists", {
      period: "90d", tz: "Europe/Moscow", limit: 48, offset: 0,
    })
    expect(fetchUserTop).toHaveBeenNthCalledWith(2, "bob", "artists", {
      period: "90d", tz: "Europe/Moscow", limit: 48, offset: 48,
    })
    await waitFor(() => expect(result.current.hasNextPage).toBe(false))
  })

  it("pages the period's top tracks in rows of 50", async () => {
    fetchUserTopTracks
      .mockResolvedValueOnce({ items: [], total: 70, next_offset: 50 })
      .mockResolvedValueOnce({ items: [], total: 70, next_offset: null })
    const { result } = renderHook(() => useUserTopTracksList("bob", "7d"), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.hasNextPage).toBe(true)
    await act(async () => {
      await result.current.fetchNextPage()
    })

    expect(fetchUserTopTracks).toHaveBeenNthCalledWith(1, "bob", {
      period: "7d", tz: "Europe/Moscow", limit: 50, offset: 0,
    })
    expect(fetchUserTopTracks).toHaveBeenNthCalledWith(2, "bob", {
      period: "7d", tz: "Europe/Moscow", limit: 50, offset: 50,
    })
  })

  it("pages one kind of likes", async () => {
    fetchUserLikesOfKind.mockResolvedValue({ items: [], total: 1, next_offset: null })
    const { result } = renderHook(() => useUserLikesList("bob", "releases"), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(fetchUserLikesOfKind).toHaveBeenCalledWith("bob", "releases", { limit: 48, offset: 0 })
    expect(result.current.hasNextPage).toBe(false)
  })
})

describe("personal recommendations of a profile", () => {
  beforeEach(() => {
    fetchUserMixes.mockReset().mockResolvedValue({ items: [], total: 40, next_offset: 16 })
    fetchUserAlbumsForYou.mockReset().mockResolvedValue({ items: [], total: 40, next_offset: 16 })
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  })

  it("preview mixes and albums with the common shelf size", async () => {
    renderHook(() => useUserMixes("bob"), { wrapper })
    renderHook(() => useUserAlbumsForYou("bob"), { wrapper })

    await waitFor(() => expect(fetchUserAlbumsForYou).toHaveBeenCalled())
    expect(fetchUserMixes).toHaveBeenCalledWith("bob", { limit: 16 })
    expect(fetchUserAlbumsForYou).toHaveBeenCalledWith("bob", { limit: 16 })
  })

  it("keeps the two shelves and each user's cache apart", async () => {
    renderHook(() => useUserMixes("Bob"), { wrapper })
    renderHook(() => useUserAlbumsForYou("Bob"), { wrapper })
    renderHook(() => useUserMixes("carol"), { wrapper })

    await waitFor(() => expect(fetchUserMixes).toHaveBeenCalledTimes(2))
    expect(fetchUserAlbumsForYou).toHaveBeenCalledTimes(1)
    // The username is case-insensitive in the key, like the other profile queries.
    const keys = queryClient.getQueryCache().getAll().map((query) => query.queryKey)
    expect(keys).toContainEqual([...PROFILE_QUERY_KEY, "bob", "mixes", 16])
    expect(keys).toContainEqual([...PROFILE_QUERY_KEY, "bob", "albums-for-you", 16])
  })

  it("pages the full lists", async () => {
    const mixes = renderHook(() => useUserMixesList("bob"), { wrapper })
    const albums = renderHook(() => useUserAlbumsForYouList("bob"), { wrapper })

    await waitFor(() => expect(mixes.result.current.isSuccess).toBe(true))
    await waitFor(() => expect(albums.result.current.isSuccess).toBe(true))
    expect(fetchUserMixes).toHaveBeenCalledWith("bob", { limit: 48, offset: 0 })
    expect(fetchUserAlbumsForYou).toHaveBeenCalledWith("bob", { limit: 48, offset: 0 })
    expect(mixes.result.current.hasNextPage).toBe(true)
  })
})

describe("useSetMyAvatar", () => {
  beforeEach(() => {
    setMyAvatar.mockReset()
    setMyAvatar.mockResolvedValue({ avatar: "a05" })
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  })

  it("saves the key and invalidates profile and people queries", async () => {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    const { result } = renderHook(() => useSetMyAvatar(), { wrapper })

    await act(async () => {
      await result.current.mutateAsync("a05")
    })

    expect(setMyAvatar).toHaveBeenCalledWith("a05")
    expect(invalidate).toHaveBeenCalledWith({ queryKey: PROFILE_QUERY_KEY })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: PEOPLE_QUERY_KEY })
  })

  it("invalidates nothing when the save fails", async () => {
    setMyAvatar.mockRejectedValueOnce(new Error("422"))
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    const { result } = renderHook(() => useSetMyAvatar(), { wrapper })

    await act(async () => {
      await result.current.mutateAsync("zz").catch(() => {})
    })

    expect(invalidate).not.toHaveBeenCalled()
  })
})

describe("useRefreshListensOnPlayChange", () => {
  const play = (trackId: number, title: string): PersonNowPlaying => ({
    track_id: trackId, title, artists: "", state: "playing", track: null,
  })

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  })

  function renderRefresh(initial: PersonNowPlaying | null | undefined) {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    const view = renderHook(
      ({ nowPlaying }: { nowPlaying: PersonNowPlaying | null | undefined }) =>
        useRefreshListensOnPlayChange("Alice", nowPlaying),
      { wrapper, initialProps: { nowPlaying: initial } },
    )
    return { invalidate, ...view }
  }

  it("refetches the recent listens and the history when the next track starts", () => {
    const { invalidate, rerender } = renderRefresh(play(1, "One"))
    expect(invalidate).not.toHaveBeenCalled()

    rerender({ nowPlaying: play(2, "Two") })

    expect(invalidate).toHaveBeenCalledWith({ queryKey: [...PROFILE_QUERY_KEY, "alice", "stats"] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: [...PROFILE_QUERY_KEY, "alice", "listens"] })
  })

  it("refetches when playback stops", () => {
    const { invalidate, rerender } = renderRefresh(play(1, "One"))

    rerender({ nowPlaying: null })

    expect(invalidate).toHaveBeenCalledTimes(2)
  })

  it("ignores polls of the same play and the first answer of the people list", () => {
    const { invalidate, rerender } = renderRefresh(undefined)

    rerender({ nowPlaying: play(1, "One") })
    rerender({ nowPlaying: play(1, "One") })

    expect(invalidate).not.toHaveBeenCalled()
  })
})
