import { useInfiniteQuery } from "@tanstack/react-query"
import { apiFetch, apiUrl } from "../client"
import type { Shelf } from "../types"

interface ShelfPage extends Shelf {
  limit: number
  offset: number
  next_offset: number | null
}

function fetchShelfPage(key: string, limit: number, offset: number): Promise<ShelfPage> {
  return apiFetch(apiUrl(`/api/v1/dashboard/shelves/${key}`, { limit, offset }))
}

export function useShelf(key: string, pageSize = 48, refetchInterval?: number) {
  return useInfiniteQuery({
    queryKey: ["shelf", key, pageSize],
    queryFn: ({ pageParam = 0 }) => fetchShelfPage(key, pageSize, pageParam as number),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    staleTime: 60_000,
    refetchInterval,
  })
}
