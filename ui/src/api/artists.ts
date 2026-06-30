import { apiFetch, apiUrl } from "./client"
import type { ArtistDiscographyResponse, ArtistTopTracksResponse, ArtistResponse } from "./types"

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
