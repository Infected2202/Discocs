import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ApiError } from "../client"
import {
  fetchUserLikes,
  fetchUserLikesOfKind,
  fetchUserListens,
  fetchUserPlaylists,
  fetchUserProfile,
  fetchUserTop,
  setMyAvatar,
  viewerTimeZone,
  type ProfileLikeKind,
  type ProfilePeriod,
  type ProfileTopKind,
} from "../profile"
import { SHELF_PREVIEW_LIMIT } from "@/lib/shelves"
import { PEOPLE_QUERY_KEY } from "./usePeople"
import { usePagedList } from "./usePagedList"

/** Every query of every profile page lives under this prefix. */
export const PROFILE_QUERY_KEY = ["profile"] as const
export const LISTENS_PAGE_SIZE = 50
// Like every horizontal shelf, the profile's likes and playlists shelves
// preview SHELF_PREVIEW_LIMIT cards; the rest is on their full lists.
const LIKES_LIMIT = SHELF_PREVIEW_LIMIT
const PLAYLISTS_LIMIT = SHELF_PREVIEW_LIMIT

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
    queryKey: [...PROFILE_QUERY_KEY, username.toLowerCase(), "playlists", PLAYLISTS_LIMIT],
    queryFn: () => fetchUserPlaylists(username, { limit: PLAYLISTS_LIMIT }),
    staleTime: 60_000,
    retry: retryUnlessClientError,
  })
}

/** Full list of a period's top artists/releases (`/u/:username/top/:kind`). */
export function useUserTopList(username: string, kind: ProfileTopKind, period: ProfilePeriod) {
  const tz = viewerTimeZone()
  return usePagedList(
    [...PROFILE_QUERY_KEY, username.toLowerCase(), "top", kind, period, tz],
    ({ limit, offset }) => fetchUserTop(username, kind, { period, tz, limit, offset }),
    { staleTime: 30_000, retry: retryUnlessClientError },
  )
}

/** Full list of one kind of likes (`/u/:username/likes/:kind`). */
export function useUserLikesList(username: string, kind: ProfileLikeKind) {
  return usePagedList(
    [...PROFILE_QUERY_KEY, username.toLowerCase(), "likes-list", kind],
    ({ limit, offset }) => fetchUserLikesOfKind(username, kind, { limit, offset }),
    { retry: retryUnlessClientError },
  )
}

/** Full list of the user's playlists (`/u/:username/playlists`). */
export function useUserPlaylistsList(username: string) {
  return usePagedList(
    [...PROFILE_QUERY_KEY, username.toLowerCase(), "playlists-list"],
    ({ limit, offset }) => fetchUserPlaylists(username, { limit, offset }),
    { retry: retryUnlessClientError },
  )
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
