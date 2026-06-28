import { apiFetch } from "./client"
import type {
  RelatedDiscographyResponse,
  ReleaseAvailabilityStub,
  ReleaseResponse,
  ReleaseTracksResponse,
} from "./types"

export function fetchRelease(id: number): Promise<ReleaseResponse> {
  return apiFetch(`/api/v1/releases/${id}`)
}

export function fetchReleaseTracks(id: number): Promise<ReleaseTracksResponse> {
  return apiFetch(`/api/v1/releases/${id}/tracks`)
}

export function fetchReleaseRelated(id: number): Promise<RelatedDiscographyResponse> {
  return apiFetch(`/api/v1/releases/${id}/related-discography`)
}

export function fetchReleaseRecommendations(id: number): Promise<ReleaseAvailabilityStub> {
  return apiFetch(`/api/v1/releases/${id}/recommendations`)
}
