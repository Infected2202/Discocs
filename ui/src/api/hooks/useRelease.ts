import { useQuery } from "@tanstack/react-query"
import { usePagedList } from "./usePagedList"
import {
  fetchRelease,
  fetchReleaseTracks,
  fetchReleaseRelated,
  fetchReleaseRecommendations,
} from "../releases"

export function useRelease(id: number) {
  return useQuery({
    queryKey: ["release", id],
    queryFn: () => fetchRelease(id),
  })
}

export function useReleaseTracks(id: number) {
  return useQuery({
    queryKey: ["release", id, "tracks"],
    queryFn: () => fetchReleaseTracks(id),
  })
}

export function useReleaseRelated(id: number) {
  return useQuery({
    queryKey: ["release", id, "related"],
    queryFn: () => fetchReleaseRelated(id),
  })
}

/** Full list of "more from these artists" (`/releases/:id/related`). */
export function useReleaseRelatedList(id: number) {
  return usePagedList(["release", id, "related-list"], ({ limit, offset }) => fetchReleaseRelated(id, limit, offset))
}

/** Full list of recommended albums (`/releases/:id/recommendations`). */
export function useReleaseRecommendationsList(id: number) {
  return usePagedList(
    ["release", id, "recommendations-list"],
    ({ limit, offset }) => fetchReleaseRecommendations(id, limit, offset),
  )
}

export function useReleaseRecommendations(id: number) {
  return useQuery({
    queryKey: ["release", id, "recommendations"],
    queryFn: () => fetchReleaseRecommendations(id),
  })
}
