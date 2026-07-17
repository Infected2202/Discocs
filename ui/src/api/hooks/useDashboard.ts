import { useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchDashboard } from "../dashboard"
import { apiFetch, apiUrl } from "../client"
import type { DashboardResponse, Shelf, ShelfItem } from "../types"
import { useEffect } from "react"

type HistoryShelf = Shelf & { items: ShelfItem[] }

async function syncPlayState(): Promise<void> {
  await apiFetch("/api/v1/navidrome/play-state/refresh", { method: "POST" })
}

function replaceHistoryShelf(dashboard: DashboardResponse, items: ShelfItem[]): DashboardResponse {
  return {
    ...dashboard,
    shelves: dashboard.shelves.map((shelf) =>
      shelf.key === "history" ? { ...shelf, items } : shelf
    ),
  }
}

async function refreshHistoryShelf(limit: number): Promise<HistoryShelf> {
  try {
    await syncPlayState()
  } catch { /* keep the locally cached history when Navidrome is unavailable */ }
  return apiFetch(apiUrl("/api/v1/dashboard/shelves/history", { limit }))
}

async function fetchSyncedDashboard(limit: number): Promise<DashboardResponse> {
  try {
    await syncPlayState()
  } catch { /* dashboard remains usable when Navidrome is unavailable */ }
  return fetchDashboard(limit)
}

export function useDashboard(limit = 12) {
  const queryClient = useQueryClient()

  // Refresh history shelf every 60s
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const shelf = await refreshHistoryShelf(limit)
        queryClient.setQueryData(["dashboard", limit], (old: DashboardResponse | undefined) =>
          old ? replaceHistoryShelf(old, shelf.items) : old
        )
      } catch { /* ignore */ }
    }, 60_000)
    return () => clearInterval(interval)
  }, [limit, queryClient])

  return useQuery({
    queryKey: ["dashboard", limit],
    queryFn: () => fetchSyncedDashboard(limit),
    staleTime: 60_000,
  })
}
