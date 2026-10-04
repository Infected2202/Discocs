import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchLabel, fetchLabelReleases, setLabelLiked } from "../labels"
import type { LabelReleaseSort, LabelResponse } from "../types"

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

/**
 * Like/unlike a label. Label likes are local to discocs (Navidrome has no
 * label stars), so this is a plain mutation — not the navidromeStore mirror.
 * The heart flips optimistically; liked labels sort first on the shelf, so the
 * dashboard and the labels shelf are refetched afterwards.
 */
export function useToggleLabelLike(id: number) {
  const queryClient = useQueryClient()
  const key = ["label", id]

  return useMutation({
    mutationFn: (liked: boolean) => setLabelLiked(id, liked),
    onMutate: async (liked: boolean) => {
      await queryClient.cancelQueries({ queryKey: key, exact: true })
      const previous = queryClient.getQueryData<LabelResponse>(key)
      if (previous) {
        queryClient.setQueryData<LabelResponse>(key, { ...previous, label: { ...previous.label, liked } })
      }
      return { previous }
    },
    onError: (_error, _liked, context) => {
      if (context?.previous) queryClient.setQueryData(key, context.previous)
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: key, exact: true }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["shelf", "labels"] }),
      ])
    },
  })
}
