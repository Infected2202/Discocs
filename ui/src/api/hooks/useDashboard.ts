import { useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchDashboard } from "../dashboard"
import { apiFetch, apiUrl } from "../client"
import type { Shelf } from "../types"
import { useEffect } from "react"

export function useDashboard(limit = 12) {
  const queryClient = useQueryClient()

  // Refresh history shelf every 60s
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const shelf: Shelf & { items: unknown[] } = await apiFetch(apiUrl("/api/v1/dashboard/shelves/history", { limit }))
        queryClient.setQueryData(["dashboard", limit], (old: { shelves: (Shelf & { key: string })[] } | undefined) => {
          if (!old) return old
          return {
            ...old,
            shelves: old.shelves.map((s) => s.key === "history" ? { ...s, items: shelf.items } : s),
          }
        })
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
