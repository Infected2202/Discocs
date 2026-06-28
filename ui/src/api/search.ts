import { apiFetch, apiUrl } from "./client"
import type { SearchResponse } from "./types"

export type SearchType = "all" | "artist" | "release" | "track"

export function fetchSearch(
  query: string,
  type: SearchType = "all",
  limit = 8,
  offset = 0,
): Promise<SearchResponse> {
  return apiFetch(apiUrl("/api/v1/search", { q: query, type, limit, offset }))
}
