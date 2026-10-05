import { useQuery } from "@tanstack/react-query"
import { usePagedList } from "./usePagedList"
import {
  fetchArtist, fetchArtistDiscography, fetchArtistLabels, fetchArtistSimilar, fetchArtistTopTracks, type DiscographySort,
} from "../artists"

export function useArtist(id: number) {
  return useQuery({
    queryKey: ["artist", id],
    queryFn: () => fetchArtist(id),
  })
}

export function useArtistDiscography(id: number, sort: DiscographySort = "release_date_desc") {
  return useQuery({
    queryKey: ["artist", id, "discography", sort],
    queryFn: () => fetchArtistDiscography(id, sort),
  })
}

export function useArtistTopTracks(id: number) {
  return useQuery({
    queryKey: ["artist", id, "top-tracks"],
    queryFn: () => fetchArtistTopTracks(id),
  })
}

/** Full list of similar artists (`/artists/:id/similar`). */
export function useArtistSimilarList(id: number) {
  return usePagedList(["artist", id, "similar-list"], ({ limit, offset }) => fetchArtistSimilar(id, limit, offset))
}

export function useArtistSimilar(id: number) {
  return useQuery({
    queryKey: ["artist", id, "similar"],
    queryFn: () => fetchArtistSimilar(id),
  })
}

/** Labels of the artist's releases — the «Лейблы артиста» shelf at the bottom of the artist page. */
export function useArtistLabels(id: number) {
  return useQuery({
    queryKey: ["artist", id, "labels"],
    queryFn: () => fetchArtistLabels(id),
  })
}
