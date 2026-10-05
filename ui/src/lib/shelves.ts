/**
 * Shared rules of horizontal shelves (docs/web-ui.md «Полки»).
 *
 * Every horizontal shelf previews the same number of cards — two slider pages
 * at the widest 8-column layout (useColumns). The backend has the same
 * constant (app/services/shelves.py) as the default preview limit.
 */
export const SHELF_PREVIEW_LIMIT = 16

/** Page size of a shelf's full list (infinite scroll). */
export const FULL_LIST_PAGE_SIZE = 48

/** One page of a paginated list, as every full-list endpoint returns it. */
export interface PagedList<T> {
  items: T[]
  total: number
  limit?: number
  offset?: number
  next_offset: number | null
}
