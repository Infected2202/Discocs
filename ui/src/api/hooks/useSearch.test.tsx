import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { useInfiniteSearch } from "./useSearch"
import type { SearchResponse } from "../types"

const fetchSearch = vi.fn()

vi.mock("../search", () => ({
  fetchSearch: (...args: unknown[]) => fetchSearch(...args),
}))

function createWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { readonly children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

function releasesResponse(total: number, offset: number, limit: number, count: number): SearchResponse {
  const nextOffset = offset + limit < total ? offset + limit : null
  return {
    query: "fabric",
    top_result: null,
    groups: [
      { type: "artists", title: "Artists", items: [], total: 0, next_offset: null },
      { type: "tracks", title: "Tracks", items: [], total: 0, next_offset: null },
      {
        type: "releases",
        title: "Releases",
        items: Array.from({ length: count }, (_, i) => ({ id: offset + i, title: `Release ${offset + i}` })),
        total,
        next_offset: nextOffset,
      },
    ],
  } as unknown as SearchResponse
}

describe("useInfiniteSearch", () => {
  afterEach(() => {
    fetchSearch.mockReset()
  })

  it("paginates through every match of the requested type using next_offset", async () => {
    fetchSearch
      .mockResolvedValueOnce(releasesResponse(49, 0, 50, 49))
      .mockResolvedValueOnce(releasesResponse(49, 50, 50, 0))

    const { result } = renderHook(() => useInfiniteSearch("fabric", "release", true, 50), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    // A single 49-item page already exhausts the total — no further page needed.
    expect(result.current.hasNextPage).toBe(false)
    expect(fetchSearch).toHaveBeenCalledTimes(1)
    expect(fetchSearch).toHaveBeenCalledWith("fabric", "release", 50, 0)
  })

  it("keeps fetching while next_offset is present, stopping once the total is covered", async () => {
    fetchSearch
      .mockResolvedValueOnce(releasesResponse(75, 0, 50, 50))
      .mockResolvedValueOnce(releasesResponse(75, 50, 50, 25))

    const { result } = renderHook(() => useInfiniteSearch("fabric", "release", true, 50), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.hasNextPage).toBe(true)

    await result.current.fetchNextPage()
    await waitFor(() => expect(result.current.hasNextPage).toBe(false))

    expect(fetchSearch).toHaveBeenNthCalledWith(1, "fabric", "release", 50, 0)
    expect(fetchSearch).toHaveBeenNthCalledWith(2, "fabric", "release", 50, 50)
    const allItems = result.current.data?.pages.flatMap(
      (p) => p.groups.find((g) => g.type === "releases")?.items ?? [],
    )
    expect(allItems).toHaveLength(75)
  })

  it("does not fetch while disabled", () => {
    renderHook(() => useInfiniteSearch("fabric", "release", false, 50), {
      wrapper: createWrapper(),
    })

    expect(fetchSearch).not.toHaveBeenCalled()
  })
})
