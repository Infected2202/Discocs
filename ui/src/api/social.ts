import { apiFetch } from "./client"
import type { PresenceState } from "./playback"

/** What another user plays right now (GET /api/v1/social/people, docs/social.md). */
export interface PersonNowPlaying {
  /** Our track id, or null when Navidrome's song is not mapped to a discocs track. */
  track_id: number | null
  title: string
  /** Display string, comma-joined. */
  artists: string
  state: Extract<PresenceState, "starting" | "playing">
}

export interface Person {
  username: string
  /** Built-in avatar key (app/avatars.py ↔ ui/src/assets/avatars/<key>.webp). */
  avatar: string
  now_playing: PersonNowPlaying | null
}

export interface PeopleResponse {
  items: Person[]
}

export function fetchPeople(): Promise<PeopleResponse> {
  return apiFetch("/api/v1/social/people")
}
