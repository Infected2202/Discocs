import { useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchDashboard } from "../dashboard"
import { apiFetch, apiUrl } from "../client"
import type { DashboardResponse, Shelf, ShelfItem } from "../types"
import { useEffect } from "react"

type HistoryShelf = Shelf & { items: ShelfItem[] }

function replaceHistoryShelf(dashboard: DashboardResponse, items: ShelfItem[]): DashboardResponse {
  return {
    ...dashboard,
    shelves: dashboard.shelves.map((shelf) =>
      shelf.key === "history" ? { ...shelf, items } : shelf
    ),
  }
}

async function refreshHistoryShelf(limit: number): Promise<HistoryShelf> {
  return apiFetch(apiUrl("/api/v1/dashboard/shelves/history", { limit }))
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
    queryFn: () => fetchDashboard(limit),
    staleTime: Infinity,
  })
}
