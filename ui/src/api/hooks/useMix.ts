import { useQuery } from "@tanstack/react-query"
import { fetchMix, fetchMixes } from "../mixes"

export function useMixes(limit = 50, offset = 0) {
  return useQuery({
    queryKey: ["mixes", limit, offset],
    queryFn: () => fetchMixes(limit, offset),
    staleTime: 60_000,
  })
}

export function useMix(id: string) {
  return useQuery({
    queryKey: ["mix", id],
    queryFn: () => fetchMix(id),
  })
}
