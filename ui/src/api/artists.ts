import { apiFetch, apiUrl } from "./client"
import type {
  ArtistDiscographyResponse, ArtistLabelsResponse, ArtistTopTracksResponse, ArtistResponse, ArtistSimilarResponse,
} from "./types"
import { SHELF_PREVIEW_LIMIT } from "@/lib/shelves"

/** One page of similar artists: the shelf previews the first, the full list pages on. */
export type ArtistSimilarPage = ArtistSimilarResponse & {
  total: number
  limit: number
  offset: number
  next_offset: number | null
}

export function fetchArtist(id: number): Promise<ArtistResponse> {
  return apiFetch(`/api/v1/artists/${id}`)
}

export type DiscographySort = "release_date_desc" | "release_date_asc" | "title" | "popularity"

export function fetchArtistDiscography(
  id: number,
  sort: DiscographySort = "release_date_desc",
): Promise<ArtistDiscographyResponse> {
  return apiFetch(apiUrl(`/api/v1/artists/${id}/discography`, { sort }))
}

export function fetchArtistTopTracks(id: number): Promise<ArtistTopTracksResponse> {
  return apiFetch(`/api/v1/artists/${id}/top-tracks`)
}

export function fetchArtistSimilar(
  id: number,
  limit = SHELF_PREVIEW_LIMIT,
  offset?: number,
): Promise<ArtistSimilarPage> {
  return apiFetch(apiUrl(`/api/v1/artists/${id}/similar`, { limit, offset }))
}

export function fetchArtistLabels(id: number): Promise<ArtistLabelsResponse> {
  return apiFetch(`/api/v1/artists/${id}/labels`)
}
