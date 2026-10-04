import { useQuery } from "@tanstack/react-query"
import { ApiError } from "../client"
import { fetchPeople } from "../social"

export const PEOPLE_QUERY_KEY = ["social", "people"] as const
/** Matches the backend's ~5 s now-playing cache: fresh enough, cheap enough. */
export const PEOPLE_REFETCH_INTERVAL_MS = 15_000

export function peopleQueryOptions() {
  return {
    queryKey: PEOPLE_QUERY_KEY,
    queryFn: fetchPeople,
    staleTime: 10_000,
    refetchInterval: PEOPLE_REFETCH_INTERVAL_MS,
    // Hidden tab → no polling; react-query refetches on focus anyway.
    refetchIntervalInBackground: false,
    retry: (failureCount: number, error: Error) => {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
      return failureCount < 1
    },
  }
}

/** Other discocs users with their live "now playing" (the «Люди» shelf, Ф4). */
export function usePeople() {
  return useQuery(peopleQueryOptions())
}
