import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ApiError } from "../client"
import {
  fetchUserLikes,
  fetchUserListens,
  fetchUserPlaylists,
  fetchUserProfile,
  setMyAvatar,
  viewerTimeZone,
  type ProfilePeriod,
} from "../profile"
import { PEOPLE_QUERY_KEY } from "./usePeople"

/** Every query of every profile page lives under this prefix. */
export const PROFILE_QUERY_KEY = ["profile"] as const
export const LISTENS_PAGE_SIZE = 50
const LIKES_LIMIT = 24

/** 404 (unknown user) and 403 won't change on retry; otherwise the app default (one retry). */
export function retryUnlessClientError(failureCount: number, error: Error) {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
  return failureCount < 1
}

/** Header, period stats, tops and recent listens (the tz is the viewer's). */
export function useUserProfile(username: string, period: ProfilePeriod) {
  const tz = viewerTimeZone()
  return useQuery({
    queryKey: [...PROFILE_QUERY_KEY, username.toLowerCase(), "stats", period, tz],
    queryFn: () => fetchUserProfile(username, period, tz),
    // Switching the period keeps the old numbers on screen until the new ones land.
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: retryUnlessClientError,
  })
}

/** Full listening history, newest first, page by page. */
export function useUserListens(username: string, pageSize = LISTENS_PAGE_SIZE) {
  return useInfiniteQuery({
    queryKey: [...PROFILE_QUERY_KEY, username.toLowerCase(), "listens", pageSize],
    queryFn: ({ pageParam }) => fetchUserListens(username, { limit: pageSize, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    retry: retryUnlessClientError,
  })
}

export function useUserLikes(username: string) {
  return useQuery({
    queryKey: [...PROFILE_QUERY_KEY, username.toLowerCase(), "likes", LIKES_LIMIT],
    queryFn: () => fetchUserLikes(username, { limit: LIKES_LIMIT }),
    staleTime: 60_000,
    retry: retryUnlessClientError,
  })
}

export function useUserPlaylists(username: string) {
  return useQuery({
    queryKey: [...PROFILE_QUERY_KEY, username.toLowerCase(), "playlists"],
    queryFn: () => fetchUserPlaylists(username),
    staleTime: 60_000,
    retry: retryUnlessClientError,
  })
}

/** Save the current user's avatar; profile headers and the people shelf show it. */
export function useSetMyAvatar() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (key: string) => setMyAvatar(key),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: PROFILE_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: PEOPLE_QUERY_KEY }),
      ])
    },
  })
}
