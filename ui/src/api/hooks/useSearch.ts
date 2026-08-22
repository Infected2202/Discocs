import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { fetchSearch, type SearchType } from "../search"
import type { SearchResponse } from "../types"

export function useSearch(query: string, type: SearchType = "all", limit = 8, offset = 0) {
  return useQuery({
    queryKey: ["search", query, type, limit, offset],
    queryFn: () => fetchSearch(query, type, limit, offset),
    enabled: query.trim().length > 0,
    staleTime: 30_000,
  })
}

const GROUP_KEY_BY_TYPE: Record<Exclude<SearchType, "all">, string> = {
  artist: "artists",
  release: "releases",
  track: "tracks",
}

/** Paginates a single result type (all of its matches, not just a preview page). */
export function useInfiniteSearch(
  query: string,
  type: Exclude<SearchType, "all">,
  enabled: boolean,
  pageSize = 50,
) {
  return useInfiniteQuery({
    queryKey: ["search", "infinite", query, type, pageSize],
    queryFn: ({ pageParam }): Promise<SearchResponse> => fetchSearch(query, type, pageSize, pageParam),
    initialPageParam: 0,
    getNextPageParam: (last) => last.groups.find((g) => g.type === GROUP_KEY_BY_TYPE[type])?.next_offset ?? undefined,
    enabled: enabled && query.trim().length > 0,
    staleTime: 30_000,
  })
}
