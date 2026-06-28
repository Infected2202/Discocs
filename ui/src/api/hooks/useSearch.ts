import { useQuery } from "@tanstack/react-query"
import { fetchSearch, type SearchType } from "../search"

export function useSearch(query: string, type: SearchType = "all", limit = 8, offset = 0) {
  return useQuery({
    queryKey: ["search", query, type, limit, offset],
    queryFn: () => fetchSearch(query, type, limit, offset),
    enabled: query.trim().length > 0,
    staleTime: 30_000,
  })
}
