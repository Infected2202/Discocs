import { useQuery } from "@tanstack/react-query"
import { fetchArtist, fetchArtistDiscography, fetchArtistSimilar, fetchArtistTopTracks, type DiscographySort } from "../artists"

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

export function useArtistSimilar(id: number) {
  return useQuery({
    queryKey: ["artist", id, "similar"],
    queryFn: () => fetchArtistSimilar(id),
  })
}
