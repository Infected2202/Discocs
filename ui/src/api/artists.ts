import { apiFetch, apiUrl } from "./client"
import type { ArtistDiscographyResponse, ArtistTopTracksResponse, ArtistResponse, ArtistSimilarResponse } from "./types"

export function fetchArtist(id: number): Promise<ArtistResponse> {
  return apiFetch(`/api/v1/artists/${id}`)
}

export type DiscographySort = "release_date_desc" | "release_date_asc" | "title"

export function fetchArtistDiscography(
  id: number,
  sort: DiscographySort = "release_date_desc",
): Promise<ArtistDiscographyResponse> {
  return apiFetch(apiUrl(`/api/v1/artists/${id}/discography`, { sort }))
}

export function fetchArtistTopTracks(id: number): Promise<ArtistTopTracksResponse> {
  return apiFetch(`/api/v1/artists/${id}/top-tracks`)
}

export function fetchArtistSimilar(id: number, limit = 16): Promise<ArtistSimilarResponse> {
  return apiFetch(apiUrl(`/api/v1/artists/${id}/similar`, { limit }))
}
