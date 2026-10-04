import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { PEOPLE_QUERY_KEY } from "./usePeople"
import { PROFILE_QUERY_KEY, useSetMyAvatar, useUserListens, useUserProfile } from "./useProfile"
import type { ProfilePeriod } from "../profile"

const fetchUserProfile = vi.fn()
const fetchUserListens = vi.fn()
const setMyAvatar = vi.fn()

vi.mock("../profile", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../profile")>()
  return {
    ...actual,
    fetchUserProfile: (...args: unknown[]) => fetchUserProfile(...args),
    fetchUserListens: (...args: unknown[]) => fetchUserListens(...args),
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
