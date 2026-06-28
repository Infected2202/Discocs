import { useQuery } from "@tanstack/react-query"
import { ApiError } from "../client"
import { fetchNavidromeSettings } from "../settings"

export type NavidromeStatus = "connected" | "disconnected" | "unknown"

export function useNavidromeStatus(): { status: NavidromeStatus; isLoading: boolean } {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["navidrome-settings"],
    queryFn: fetchNavidromeSettings,
    staleTime: 60_000,
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
      return failureCount < 2
    },
  })

  if (isLoading) return { status: "unknown", isLoading: true }
  if (isError || !data) return { status: "disconnected", isLoading: false }

  const connected = Boolean(data.url && data.user && data.password_set)
  return { status: connected ? "connected" : "disconnected", isLoading: false }
}
