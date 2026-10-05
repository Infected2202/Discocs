import { apiFetch, apiUrl } from "./client"
import type {
  RelatedDiscographyResponse,
  ReleaseAvailabilityStub,
  ReleaseResponse,
  ReleaseTracksResponse,
} from "./types"
import { SHELF_PREVIEW_LIMIT } from "@/lib/shelves"

interface PageFields {
  total: number
  limit: number
  offset: number
  next_offset: number | null
}

/** One page of "more from these artists" (shelf preview or full list). */
export type ReleaseRelatedPage = RelatedDiscographyResponse & PageFields
/** One page of recommended albums (shelf preview or full list). */
export type ReleaseRecommendationsPage = ReleaseAvailabilityStub & PageFields

export function fetchRelease(id: number): Promise<ReleaseResponse> {
  return apiFetch(`/api/v1/releases/${id}`)
}

export function fetchReleaseTracks(id: number): Promise<ReleaseTracksResponse> {
  return apiFetch(`/api/v1/releases/${id}/tracks`)
}

export function fetchReleaseRelated(
  id: number,
  limit = SHELF_PREVIEW_LIMIT,
  offset?: number,
): Promise<ReleaseRelatedPage> {
  return apiFetch(apiUrl(`/api/v1/releases/${id}/related-discography`, { limit, offset }))
}

export function fetchReleaseRecommendations(
  id: number,
  limit = SHELF_PREVIEW_LIMIT,
  offset?: number,
): Promise<ReleaseRecommendationsPage> {
  return apiFetch(apiUrl(`/api/v1/releases/${id}/recommendations`, { limit, offset }))
}
