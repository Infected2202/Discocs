import { useQuery } from "@tanstack/react-query"
import { fetchMix, fetchMixes } from "../mixes"
import { fetchUserMix } from "../profile"

export function useMixes(limit = 50, offset = 0) {
  return useQuery({
    queryKey: ["mixes", limit, offset],
    queryFn: () => fetchMixes(limit, offset),
    staleTime: 60_000,
  })
}

/** A mix; with `username` it is read on that user's profile (someone else's mix). */
export function useMix(id: string, username?: string) {
  return useQuery({
    queryKey: username ? ["profile", username.toLowerCase(), "mix", id] : ["mix", id],
    queryFn: () => (username ? fetchUserMix(username, id) : fetchMix(id)),
  })
}
