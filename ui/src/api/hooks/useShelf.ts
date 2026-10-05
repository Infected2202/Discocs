import { apiFetch, apiUrl } from "../client"
import type { Shelf } from "../types"
import { FULL_LIST_PAGE_SIZE } from "@/lib/shelves"
import { usePagedList } from "./usePagedList"

interface ShelfPage extends Shelf {
  limit: number
  offset: number
  next_offset: number | null
}

export function fetchShelfPage(key: string, limit: number, offset: number): Promise<ShelfPage> {
  return apiFetch(apiUrl(`/api/v1/dashboard/shelves/${key}`, { limit, offset }))
}

/** A dashboard shelf's full list (`/shelf/:key`), page by page. */
export function useShelf(key: string, pageSize = FULL_LIST_PAGE_SIZE, refetchInterval?: number) {
  return usePagedList(["shelf", key], ({ limit, offset }) => fetchShelfPage(key, limit, offset), {
    pageSize,
    refetchInterval,
  })
}
