import { useEffect } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { getUserSettings, updateUserSettings, type UserSettings } from "@/api/settings"
import i18n from "@/i18n"

const USER_SETTINGS_QUERY_KEY = ["me", "settings"]

/**
 * Fetches the signed-in user's settings and applies the stored language to
 * i18next once loaded — the backend is the source of truth, localStorage is
 * only a cache to avoid an English flash before this resolves.
 */
export function useUserSettings() {
  const query = useQuery({
    queryKey: USER_SETTINGS_QUERY_KEY,
    queryFn: getUserSettings,
    staleTime: 60_000,
  })

  useEffect(() => {
    if (query.data?.language && query.data.language !== i18n.resolvedLanguage) {
      void i18n.changeLanguage(query.data.language)
    }
  }, [query.data?.language])

  return query
}

export function useUpdateUserSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (patch: Partial<UserSettings>) => updateUserSettings(patch),
    onSuccess: (data) => {
      queryClient.setQueryData(USER_SETTINGS_QUERY_KEY, data)
      if (data.language) void i18n.changeLanguage(data.language)
    },
  })
}
