// ----------------------------------------------------------------------------
// Shared primitives
// ----------------------------------------------------------------------------

export interface ImageRef {
  url: string | null
  source: string
  placeholder: boolean
}

export interface ArtistLink {
  id: number
  name: string
}

export interface ReleaseLink {
  id: number
  title: string
}

export interface EntityAction {
  type: string
  enabled: boolean
  endpoint: string | null
}

export interface LibraryStats {
  tracks: number
  releases: number
  liked_tracks: number
  plays: number
}

// ----------------------------------------------------------------------------
// Artist
// ----------------------------------------------------------------------------

export interface ArtistSummary {
  id: number
  name: string
  sort_name: string | null
  image: ImageRef
  library_stats: LibraryStats
}

export interface ArtistResponse {
  artist: ArtistSummary
  actions: EntityAction[]
  links: Record<string, string>
}

export interface DiscographyGroup {
  key: string
  title: string
  items: ReleaseSummary[]
}

export interface ArtistDiscographyResponse {
  artist: ArtistLink
  groups: DiscographyGroup[]
}

export interface ArtistAvailabilityStub {
  artist: ArtistLink
  items: unknown[]
  available: boolean
  basis: string
}

// ----------------------------------------------------------------------------
// Release
// ----------------------------------------------------------------------------

export interface ReleaseSummary {
  id: number
  title: string
  release_type: string
  release_type_label: string
  artists: ArtistLink[]
  release_date: string | null
  release_year: number | null
  track_count: number
  duration: number | null
  artwork: ImageRef
}

export interface ReleaseResponse {
  release: ReleaseSummary
  actions: EntityAction[]
  links: Record<string, string>
}

export interface ReleaseTrackItem {
  id: number
  title: string
  artists: ArtistLink[]
  duration: number | null
  release: ReleaseLink | null
  artwork: ImageRef
  explicit: boolean
  liked: boolean
  actions: EntityAction[]
  disc_number: number | null
  track_number: number | null
  position: number
}

export interface ReleaseTracksResponse {
  release: ReleaseLink
  items: ReleaseTrackItem[]
}

export interface RelatedDiscographyResponse {
  release: ReleaseLink
  context_artists: ArtistLink[]
  items: ReleaseSummary[]
}

export interface ReleaseAvailabilityStub {
  release: ReleaseLink
  available: boolean
  basis: string
  items: unknown[]
}

// ----------------------------------------------------------------------------
// Track (shared summary used in search / queue)
// ----------------------------------------------------------------------------

export interface TrackSummary {
  id: number
  title: string
  artists: ArtistLink[]
  duration: number | null
  release: ReleaseLink | null
  artwork: ImageRef
  explicit: boolean
  liked: boolean
  actions: EntityAction[]
}

// ----------------------------------------------------------------------------
// Search
// ----------------------------------------------------------------------------

export interface SearchTopResult {
  entity_type: string
  entity: ArtistSummary | ReleaseSummary | TrackSummary
}

export interface SearchGroup {
  type: string
  title: string
  items: (ArtistSummary | ReleaseSummary | TrackSummary)[]
  total: number
  next_offset: number | null
}

export interface SearchResponse {
  query: string
  top_result: SearchTopResult | null
  groups: SearchGroup[]
}

// ----------------------------------------------------------------------------
// Dashboard
// ----------------------------------------------------------------------------

export type ShelfItemType = "artist" | "release" | "generated_mix"

export interface ShelfItem {
  id: string                // e.g. "artist:1", "release:5", "generated_mix:abc"
  entity_type: ShelfItemType
  entity_id: number | string
  title: string
  subtitle: string | null
  artwork: ImageRef
  reason: string | null
  play_action:
    | { type: "play"; source_type: string; source_id: number | string; source_label?: string }
    | { type: "post"; endpoint: string }
    | null
}

export interface Shelf {
  key: string
  title: string
  subtitle: string | null
  items: ShelfItem[]
  total: number
}

export interface DashboardHero {
  type: string
  title: string
  subtitle: string
  available: boolean
}

export interface DashboardResponse {
  hero: DashboardHero
  shelves: Shelf[]
  settings: {
    visible_shelves: string[]
    items_per_shelf: number
  }
}

// ----------------------------------------------------------------------------
// Generated mix
// ----------------------------------------------------------------------------

export interface GeneratedMixSummary {
  id: string
  title: string
  status: string
  track_count: number
  artwork: ImageRef | null
  created_at: string
}

export interface GeneratedMixDetail extends GeneratedMixSummary {
  items: MixTrackItem[]
}

export interface MixTrackItem {
  track_id: number
  track: TrackSummary | null
  position: number
  score: number | null
  reason: string | null
}

// ----------------------------------------------------------------------------
// Playback
// ----------------------------------------------------------------------------

export interface PlaybackSession {
  id: string
  source_type: string
  source_id: number | null
  source_label: string | null
  mode: string
  status: string
  current_track_id: number | null
  current_queue_item_id: string | null
  current_track: TrackSummary | null
  autoplay_enabled: boolean
  shuffle_enabled: boolean
  repeat_mode: string
  started_at: string
  updated_at: string
  ended_at: string | null
  settings: Record<string, unknown>
  state: Record<string, unknown>
}

export interface AutoplayPoolItem {
  id: string
  track_id: number
  track: TrackSummary | null
  origin: string
  score: number | null
}

export interface QueueItem {
  id: string
  session_id: string
  track_id: number
  track: TrackSummary | null
  position: number
  origin: string
  source_type: string | null
  source_id: number | null
  status: string
  locked: boolean
  reason: string | null
  score: number | null
  created_at: string
  updated_at: string
  debug: Record<string, unknown> | null
}

export interface PlaybackQueue {
  items: QueueItem[]
  current_index: number
  current_item: QueueItem | null
  upcoming: QueueItem[]
  played: QueueItem[]
  source_items: QueueItem[]
  generated_items: QueueItem[]
  autoplay_pool: AutoplayPoolItem[]
}

export interface PlaybackEnvelope {
  session: PlaybackSession
  queue: PlaybackQueue
}

export interface PlaybackEventResponse {
  accepted: boolean
  duplicate: boolean
  event_id: string
  preference_delta: Record<string, unknown>
}

export interface AutoplayRefillResponse {
  session_id: string
  added_items: QueueItem[]
  candidate_count: number
  debug: Record<string, unknown> | null
}

// ----------------------------------------------------------------------------
// Settings
// ----------------------------------------------------------------------------

export interface NavidromeSettings {
  url: string
  user: string
  password_set: boolean
  auth_mode: string
  timeout_seconds: number
  download_mode: string
  temp_dir: string
}

export interface NavidromePingResponse {
  status: string
  version: string
  server_version: string
}
