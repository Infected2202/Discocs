import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { fetchLabel, fetchLabelReleases } from "../labels"
import type { LabelReleaseSort } from "../types"

export function useLabel(id: number) {
  return useQuery({
    queryKey: ["label", id],
    queryFn: () => fetchLabel(id),
  })
}

export function useLabelReleases(id: number, sort: LabelReleaseSort) {
  return useQuery({
    queryKey: ["label", id, "releases", sort],
    queryFn: () => fetchLabelReleases(id, sort),
    // Смена сортировки не должна мигать скелетоном — старый список висит до нового.
    placeholderData: keepPreviousData,
  })
}
