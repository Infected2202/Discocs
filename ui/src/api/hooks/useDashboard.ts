import { useQuery } from "@tanstack/react-query"
import { fetchDashboard } from "../dashboard"

export function useDashboard(limit = 12) {
  return useQuery({
    queryKey: ["dashboard", limit],
    queryFn: () => fetchDashboard(limit),
    staleTime: 60_000,
  })
}
