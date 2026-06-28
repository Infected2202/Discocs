import { useQuery } from "@tanstack/react-query"
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

export function useReleaseRecommendations(id: number) {
  return useQuery({
    queryKey: ["release", id, "recommendations"],
    queryFn: () => fetchReleaseRecommendations(id),
  })
}
