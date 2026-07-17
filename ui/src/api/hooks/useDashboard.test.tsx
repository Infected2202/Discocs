import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor, act } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { useDashboard } from "./useDashboard"
import type { DashboardResponse } from "../types"

const fetchDashboard = vi.fn()
const apiFetch = vi.fn()
const apiUrl = vi.fn()

vi.mock("../dashboard", () => ({
  fetchDashboard: (...args: unknown[]) => fetchDashboard(...args),
}))

vi.mock("../client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  apiUrl: (...args: unknown[]) => apiUrl(...args),
}))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { readonly children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe("useDashboard", () => {
  afterEach(() => {
    fetchDashboard.mockReset()
    apiFetch.mockReset()
    apiUrl.mockReset()
    vi.restoreAllMocks()
  })

  it("refreshes the cached history shelf without touching the other shelves", async () => {
    const initialDashboard: DashboardResponse = {
      hero: { type: "flow", title: "Flow", subtitle: "Start", available: true },
      settings: { visible_shelves: ["history", "recent"], items_per_shelf: 12 },
      shelves: [
        {
          key: "history",
          title: "History",
          subtitle: null,
          total: 1,
          items: [{ id: "track:1", entity_id: 1, entity_type: "track", title: "Old", subtitle: null, artwork: { url: null, source: "none", placeholder: true }, reason: null, play_action: null }],
        },
        {
          key: "recent",
          title: "Recent",
          subtitle: null,
          total: 1,
          items: [{ id: "release:2", entity_id: 2, entity_type: "release", title: "Keep", subtitle: null, artwork: { url: null, source: "none", placeholder: true }, reason: null, play_action: null }],
        },
      ],
    }

    fetchDashboard.mockResolvedValue(initialDashboard)
    apiUrl.mockReturnValue("/api/v1/dashboard/shelves/history?limit=12&offset=0")
    const refreshedShelf = {
      key: "history",
      title: "History",
      subtitle: null,
      total: 1,
      limit: 12,
      offset: 0,
      next_offset: null,
      items: [{ id: "track:3", entity_id: 3, entity_type: "track", title: "Fresh", subtitle: null, artwork: { url: null, source: "none", placeholder: true }, reason: null, play_action: null }],
    }
    apiFetch.mockImplementation((url: string) =>
      Promise.resolve(url === "/api/v1/navidrome/play-state/refresh" ? { updated_count: 1 } : refreshedShelf)
    )

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(["dashboard", 12], initialDashboard)
    let refreshInterval: (() => Promise<void>) | undefined
    vi.spyOn(globalThis, "setInterval").mockImplementation((handler, timeout) => {
      if (timeout === 60_000) {
        refreshInterval = handler as () => Promise<void>
      }
      return 1 as unknown as ReturnType<typeof setInterval>
    })

    const { result } = renderHook(() => useDashboard(12), {
      wrapper: createWrapper(queryClient),
    })

    await waitFor(() => expect(result.current.data).toEqual(initialDashboard))
    await waitFor(() => expect(refreshInterval).toBeTypeOf("function"))

    await act(async () => {
      await refreshInterval?.()
    })

    const cached = queryClient.getQueryData<DashboardResponse>(["dashboard", 12])

    expect(apiUrl).toHaveBeenCalledWith("/api/v1/dashboard/shelves/history", { limit: 12 })
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/navidrome/play-state/refresh",
      { method: "POST" },
    )
    expect(cached?.shelves[0].items).toEqual([
      expect.objectContaining({ id: "track:3", title: "Fresh" }),
    ])
    expect(cached?.shelves[1].items).toEqual([
      expect.objectContaining({ id: "release:2", title: "Keep" }),
    ])
  })

  it("syncs the active user's Navidrome play state before the initial dashboard", async () => {
    const dashboard: DashboardResponse = {
      hero: { type: "flow", title: "Flow", subtitle: "Start", available: true },
      settings: { visible_shelves: ["history"], items_per_shelf: 12 },
      shelves: [],
    }
    apiFetch.mockResolvedValue({ updated_count: 2 })
    fetchDashboard.mockResolvedValue(dashboard)

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useDashboard(12), {
      wrapper: createWrapper(queryClient),
    })

    await waitFor(() => expect(result.current.data).toEqual(dashboard))
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/navidrome/play-state/refresh",
      { method: "POST" },
    )
    expect(apiFetch.mock.invocationCallOrder[0]).toBeLessThan(
      fetchDashboard.mock.invocationCallOrder[0],
    )
  })

  it("still loads the dashboard when the Navidrome refresh fails", async () => {
    const dashboard: DashboardResponse = {
      hero: { type: "flow", title: "Flow", subtitle: "Start", available: true },
      settings: { visible_shelves: ["history"], items_per_shelf: 12 },
      shelves: [],
    }
    apiFetch.mockRejectedValue(new Error("Navidrome unavailable"))
    fetchDashboard.mockResolvedValue(dashboard)

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useDashboard(12), {
      wrapper: createWrapper(queryClient),
    })

    await waitFor(() => expect(result.current.data).toEqual(dashboard))
    expect(fetchDashboard).toHaveBeenCalledWith(12)
  })
})
