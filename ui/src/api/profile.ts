// User profile API (social features Ф3, docs/social.md "API профиля").
import { apiFetch, apiUrl } from "./client"
import type { GeneratedMixDetail, PlaybackEnvelope, PlaylistSummary, ShelfItem, TrackSummary } from "./types"
import type { PagedList } from "@/lib/shelves"

export const PROFILE_PERIODS = ["7d", "30d", "90d", "180d", "365d", "all"] as const
export type ProfilePeriod = (typeof PROFILE_PERIODS)[number]
export const DEFAULT_PROFILE_PERIOD: ProfilePeriod = "30d"

export interface ProfileTotals {
  listens: number
  artists: number
  likes: number
}

export interface ProfileHeader {
  username: string
  /** Built-in avatar key (see lib/avatars.ts). */
  avatar: string
  created_at: string
  viewer_is_owner: boolean
  totals: ProfileTotals
}

export interface ProfilePeriodWindow {
  key: ProfilePeriod
  /** The IANA zone the server actually applied (invalid → "UTC"). */
  tz: string
  since: string | null
  until: string
}

export interface ProfileDayBucket {
  /** Local date: `YYYY-MM-DD` (day bucket) or `YYYY-MM-01` (month bucket). */
  date: string
  listens: number
}

export interface ProfileGenreShare {
  label: string
  genre: string
  style: string
  listens: number
  /** 0..1 */
  share: number
}

export interface ProfileMoodShare {
  label: string
  listens: number
  share: number
}

export type ProfileShelfItem = ShelfItem & { listens: number }
export type ProfileTopTrack = TrackSummary & { listens: number }

/** One listen: the track plus its unique listen id and UTC timestamp. */
export type ListenItem = TrackSummary & { listen_id: number; listened_at: string }

export interface UserProfile {
  header: ProfileHeader
  period: ProfilePeriodWindow
  summary: { listens: number; hours: number; artists: number }
  by_day_bucket: "day" | "month"
  by_day: ProfileDayBucket[]
  /** 24 numbers: listens per local hour 0–23. */
  by_hour: number[]
  sound: { genres: ProfileGenreShare[]; moods: ProfileMoodShare[] }
  top_artists: ProfileShelfItem[]
  /** Full length of the period's top (the shelf previews the first ones). */
  top_artists_total: number
  top_releases: ProfileShelfItem[]
  top_releases_total: number
  /** The first rows of the period's top tracks (the full list is `/top/tracks`). */
  top_tracks: ProfileTopTrack[]
  top_tracks_total: number
  recent: ListenItem[]
}

export interface UserListensPage {
  items: ListenItem[]
  total: number
  limit: number
  offset: number
  next_offset: number | null
}

export interface UserLikes {
  tracks: { items: ShelfItem[]; total: number }
  releases: { items: ShelfItem[]; total: number }
  artists: { items: ShelfItem[]; total: number }
  limit: number
  offset: number
}

export interface UserPlaylists {
  items: PlaylistSummary[]
  total: number
  limit: number
  offset: number
  next_offset: number | null
}

/** Mixes and Albums For You of a profile: shelf cards, paged. */
export type UserRecommendationsPage = PagedList<ShelfItem>

/** Profile shelves with a full list (`/u/:username/top/:kind`, `/likes/:kind`). */
export const PROFILE_TOP_KINDS = ["artists", "releases"] as const
export type ProfileTopKind = (typeof PROFILE_TOP_KINDS)[number]
export const PROFILE_LIKE_KINDS = ["tracks", "releases", "artists"] as const
export type ProfileLikeKind = (typeof PROFILE_LIKE_KINDS)[number]

export type UserTopPage = PagedList<ProfileShelfItem> & { period: ProfilePeriodWindow }
export type UserTopTracksPage = PagedList<ProfileTopTrack> & { period: ProfilePeriodWindow }
export type UserLikesPage = PagedList<ShelfItem>

export function isProfilePeriod(value: string | null | undefined): value is ProfilePeriod {
  return (PROFILE_PERIODS as readonly string[]).includes(value ?? "")
}

function userPath(username: string, tail: string): string {
  return `/api/v1/users/${encodeURIComponent(username)}/${tail}`
}

export function fetchUserProfile(username: string, period: ProfilePeriod, tz: string): Promise<UserProfile> {
  return apiFetch(apiUrl(userPath(username, "profile"), { period, tz }))
}

export function fetchUserListens(
  username: string,
  params: { limit?: number; offset?: number } = {},
): Promise<UserListensPage> {
  return apiFetch(apiUrl(userPath(username, "listens"), { limit: params.limit, offset: params.offset }))
}

export function fetchUserLikes(username: string, params: { limit?: number } = {}): Promise<UserLikes> {
  return apiFetch(apiUrl(userPath(username, "likes"), { limit: params.limit }))
}

export function fetchUserPlaylists(
  username: string,
  params: { limit?: number; offset?: number } = {},
): Promise<UserPlaylists> {
  return apiFetch(apiUrl(userPath(username, "playlists"), { limit: params.limit, offset: params.offset }))
}

/** One page of the period's top artists/releases (the top shelf's full list). */
export function fetchUserTop(
  username: string,
  kind: ProfileTopKind,
  params: { period: ProfilePeriod; tz: string; limit: number; offset: number },
): Promise<UserTopPage> {
  return apiFetch(apiUrl(userPath(username, `top/${kind}`), params))
}

/** One page of a period's top tracks (the top tracks list's full page). */
export function fetchUserTopTracks(
  username: string,
  params: { period: ProfilePeriod; tz: string; limit: number; offset: number },
): Promise<UserTopTracksPage> {
  return apiFetch(apiUrl(userPath(username, "top/tracks"), params))
}

/** One page of one kind of likes (a likes shelf's full list). */
export function fetchUserLikesOfKind(
  username: string,
  kind: ProfileLikeKind,
  params: { limit: number; offset: number },
): Promise<UserLikesPage> {
  return apiFetch(apiUrl(userPath(username, `likes/${kind}`), params))
}

/**
 * The user's current generated mixes. The cards of another user's mixes open
 * the read-only page under their profile and play through the profile
 * endpoints; on one's own profile they are the ordinary ones.
 */
export function fetchUserMixes(
  username: string,
  params: { limit?: number; offset?: number } = {},
): Promise<UserRecommendationsPage> {
  return apiFetch(apiUrl(userPath(username, "mixes"), { limit: params.limit, offset: params.offset }))
}

/** The user's "Albums For You": their cached recommendations, best first. */
export function fetchUserAlbumsForYou(
  username: string,
  params: { limit?: number; offset?: number } = {},
): Promise<UserRecommendationsPage> {
  return apiFetch(apiUrl(userPath(username, "albums-for-you"), { limit: params.limit, offset: params.offset }))
}

/** One of the user's mixes with its tracks (no scores or generation settings). */
export function fetchUserMix(username: string, mixId: string): Promise<GeneratedMixDetail> {
  return apiFetch(userPath(username, `mixes/${encodeURIComponent(mixId)}`))
}

/** Play the user's mix in the viewer's own playback session. */
export function playUserMix(username: string, mixId: string): Promise<PlaybackEnvelope> {
  return apiFetch(userPath(username, `mixes/${encodeURIComponent(mixId)}/play`), { method: "POST" })
}

/** How the listen-along queue was built (docs/social.md "Слушать вместе"). */
export type ListenAlongStrategy = "queue" | "personal_source" | "no_session" | "not_in_queue"

/**
 * A new playback session of the viewer that picks up what another user plays.
 * `session.source_type` is `"listen_along"`, `source_label` the host's login.
 */
export interface ListenAlongEnvelope extends PlaybackEnvelope {
  start_track_id: number
  start_queue_item_id: string | null
  /** Seek here once the first track is loaded. */
  start_position_seconds: number
  listen_along: { host: string; strategy: ListenAlongStrategy }
}

/**
 * One-shot "pick up" of another user's playback. 400 on yourself, 404 unknown
 * user, 409 `not_playing` / `track_not_mapped`.
 */
export function listenAlong(username: string): Promise<ListenAlongEnvelope> {
  return apiFetch(userPath(username, "listen-along"), { method: "POST" })
}

/** Pick one of the built-in avatars for the current user. */
export function setMyAvatar(key: string): Promise<{ avatar: string }> {
  return apiFetch("/api/v1/me/avatar", { method: "PUT", body: JSON.stringify({ key }) })
}

/** The viewer's IANA time zone — the server buckets days/hours in it. */
export function viewerTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  } catch {
    return "UTC"
  }
}
