import { useQuery } from "@tanstack/react-query"
import { getFlowProfile } from "@/api/flow"

export function useFlowProfile(modelKey = "discogs_multi") {
  return useQuery({
    queryKey: ["flow-profile", modelKey],
    queryFn: () => getFlowProfile(modelKey),
    staleTime: 60_000,
    retry: false,
  })
}
