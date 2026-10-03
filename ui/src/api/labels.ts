import { apiFetch, apiUrl } from "./client"
import type { LabelReleaseSort, LabelReleasesResponse, LabelResponse } from "./types"

export function fetchLabel(id: number): Promise<LabelResponse> {
  return apiFetch(`/api/v1/labels/${id}`)
}

export function fetchLabelReleases(
  id: number,
  sort: LabelReleaseSort = "release_date_desc",
): Promise<LabelReleasesResponse> {
  return apiFetch(apiUrl(`/api/v1/labels/${id}/releases`, { sort }))
}
