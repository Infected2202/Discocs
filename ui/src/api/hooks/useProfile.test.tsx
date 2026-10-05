import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { PEOPLE_QUERY_KEY } from "./usePeople"
import {
  PROFILE_QUERY_KEY,
  useSetMyAvatar,
  useUserLikes,
  useUserLikesList,
  useUserListens,
  useUserPlaylists,
  useUserProfile,
  useUserTopList,
} from "./useProfile"
import type { ProfilePeriod } from "../profile"

const fetchUserProfile = vi.fn()
const fetchUserListens = vi.fn()
const fetchUserTop = vi.fn()
const fetchUserLikes = vi.fn()
const fetchUserLikesOfKind = vi.fn()
const fetchUserPlaylists = vi.fn()
const setMyAvatar = vi.fn()

vi.mock("../profile", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../profile")>()
  return {
    ...actual,
    fetchUserProfile: (...args: unknown[]) => fetchUserProfile(...args),
    fetchUserListens: (...args: unknown[]) => fetchUserListens(...args),
    fetchUserTop: (...args: unknown[]) => fetchUserTop(...args),
    fetchUserLikes: (...args: unknown[]) => fetchUserLikes(...args),
    fetchUserLikesOfKind: (...args: unknown[]) => fetchUserLikesOfKind(...args),
    fetchUserPlaylists: (...args: unknown[]) => fetchUserPlaylists(...args),
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
    fetchUserLikesOfKind.mockReset()
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  })

  it("pages the period's top in the viewer's time zone", async () => {
    fetchUserTop
      .mockResolvedValueOnce({ items: [], total: 60, next_offset: 48 })
      .mockResolvedValueOnce({ items: [], total: 60, next_offset: null })
    const { result } = renderHook(() => useUserTopList("bob", "artists", "90d"), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
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

  it("pages one kind of likes", async () => {
    fetchUserLikesOfKind.mockResolvedValue({ items: [], total: 1, next_offset: null })
    const { result } = renderHook(() => useUserLikesList("bob", "releases"), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(fetchUserLikesOfKind).toHaveBeenCalledWith("bob", "releases", { limit: 48, offset: 0 })
    expect(result.current.hasNextPage).toBe(false)
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
