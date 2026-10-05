import { useInfiniteQuery, type QueryKey } from "@tanstack/react-query"
import { FULL_LIST_PAGE_SIZE, type PagedList } from "@/lib/shelves"

export interface PageRequest {
  limit: number
  offset: number
}

interface PagedListOptions {
  readonly pageSize?: number
  readonly staleTime?: number
  readonly refetchInterval?: number
  readonly retry?: (failureCount: number, error: Error) => boolean
}

/**
 * Infinite query over any `limit`/`offset` endpoint that answers with
 * `next_offset` — the data source of a shelf's full list (FullListPage).
 */
export function usePagedList<P extends PagedList<unknown>>(
  queryKey: QueryKey,
  fetchPage: (page: PageRequest) => Promise<P>,
  { pageSize = FULL_LIST_PAGE_SIZE, staleTime = 60_000, refetchInterval, retry }: PagedListOptions = {},
) {
  return useInfiniteQuery({
    queryKey: [...queryKey, pageSize],
    queryFn: ({ pageParam }) => fetchPage({ limit: pageSize, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last: P) => last.next_offset ?? undefined,
    staleTime,
    refetchInterval,
    ...(retry ? { retry } : {}),
  })
}
