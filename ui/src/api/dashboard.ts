import { apiFetch, apiUrl } from "./client"
import type { DashboardResponse } from "./types"

export function fetchDashboard(limit = 12): Promise<DashboardResponse> {
  return apiFetch(apiUrl("/api/v1/dashboard", { limit }))
}
