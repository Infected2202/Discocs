# Data Model Overview

## Purpose

Define the future backend data model for the music web app and recommendation
system.

This is not a low-level SQL migration spec yet. It is an architecture map for
entities, relationships, API boundaries, and storage decisions.

The future web UI may be built on a different stack, so the backend should be
API-first and not tightly coupled to the current prototype UI.

## Decision Style

When a design choice has meaningful alternatives, list the options and the
recommended direction.

When the choice is straightforward, document the recommended solution directly.

## Principles

- Use stable internal IDs for all core entities.
- Keep external provider IDs in mapping tables.
- Model releases/albums explicitly instead of deriving everything from track
  text fields at request time.
- Preserve uncertainty in metadata instead of guessing too aggressively.
- Keep raw playback events as source of truth.
- Keep embeddings and heavy vector data separable from metadata/user data.
- Design APIs around domain entities: tracks, artists, releases, playlists,
  queues, events, recommendations.

## Core Entities

### Tracks

Track is the playable audio unit.

Recommended fields:

- `id`
- `path` or storage/audio source reference
- `title`
- `duration`
- `file_size`
- `mtime`
- `audio_hash`
- `release_id`
- `disc_number`
- `track_number`
- `explicit` if available
- `missing_at`
- `created_at`
- `updated_at`

Notes:

- A track does not need its own standalone page.
- Track appears inside release pages, playlists, search results, queues, mixes,
  and recommendation rows.
- One-track releases are still releases.

### Artists

Artist is a normalized entity, not only a text field on a track.

Recommended fields:

- `id`
- `name`
- `sort_name`
- `image_url` or local image reference
- `bio` optional
- `created_at`
- `updated_at`

Related tables:

- `artist_aliases`
- `track_artists`
- `release_artists`

Artist roles:

- primary artist;
- featured artist;
- remixer;
- composer/producer later if metadata supports it.

Obvious solution: keep roles flexible rather than hardcoding only one artist
string.

### Releases

Use "release" as the internal model instead of only "album".

Reason:

- UI can say album where appropriate;
- model must support album, EP, single, compilation, soundtrack, unknown;
- one-track singles still need a release container;
- compilations and featured-in sections become easier.

Recommended fields:

- `id`
- `title`
- `release_type`: album, ep, single, compilation, soundtrack, mix,
  unknown
- `release_date`
- `release_year`
- `cover_art_id` or cover path/reference
- `track_count`
- `duration`
- `label` optional, low priority for UI
- `catalog_number` optional
- `created_at`
- `updated_at`

Related tables:

- `release_artists`
- `release_tracks`
- `release_external_ids`

Release type uncertainty:

- If type is known, store it.
- If type is missing, use `unknown` and group under "Releases".
- Do not hide unknown releases.
- Weak inference from track count/duration can be stored as derived metadata
  later, but original uncertainty should remain visible to the system.

### Release Tracks

Join table between releases and tracks.

Recommended fields:

- `release_id`
- `track_id`
- `disc_number`
- `track_number`
- `position`
- `title_override` optional

This supports multi-disc releases and ordered album pages.

### Track Artists

Join table between tracks and artists.

Recommended fields:

- `track_id`
- `artist_id`
- `role`
- `position`

This supports featured-in pages, remixers, collaborations, and artist pages.

### Release Artists

Join table between releases and artists.

Recommended fields:

- `release_id`
- `artist_id`
- `role`
- `position`

This is needed to determine whether an artist is the main release artist or only
appears on tracks.

## External IDs

Use provider mapping tables rather than storing provider IDs directly on core
entities.

Recommended generic shape:

- `provider`: navidrome, musicbrainz, discogs, local, etc.
- `entity_type`: track, artist, release, playlist
- `entity_id`
- `external_id`
- `raw_json`
- `synced_at`

Current `external_tracks` can evolve into this broader mapping.

Reason:

- Navidrome IDs are useful for playback/cover integration.
- Future metadata providers may be added.
- Core app IDs remain stable even if provider IDs change.

## Playlists And Saved Collections

Playlists are user-owned ordered collections.

Recommended tables:

`playlists`

- `id`
- `user_id`
- `title`
- `description`
- `kind`: user, saved_mix, saved_autoplay_queue, generated
- `source_json`
- `is_static`
- `created_at`
- `updated_at`

`playlist_items`

- `playlist_id`
- `position`
- `track_id`
- `added_at`
- `source_json`

Options:

1. Store generated mixes as playlists immediately.
2. Store generated mixes separately and only convert to playlist when saved.

Recommendation:

- Keep generated mixes separate while they are rolling recommendations.
- Convert/copy to playlists when saved.

Reason:

- Dashboard mixes can refresh without mutating saved user artifacts.

## Generated Mixes

Generated mixes are finite recommendation playlists, usually 100 tracks.

Recommended tables:

`generated_mixes`

- `id`
- `user_id`
- `title`
- `mix_type`
- `anchor_json`
- `settings_json`
- `score_summary_json`
- `created_at`
- `updated_at`
- `expires_at`
- `saved_playlist_id`

`generated_mix_items`

- `mix_id`
- `position`
- `track_id`
- `score`
- `score_breakdown_json`
- `reason_json`

Notes:

- Unsaved generated mixes can be refreshed.
- Saved mixes should become stable playlists.
- Track duplication across dashboard mixes should be controlled at generation
  time.

## Playback Queue And Sessions

Queue/session data should be first-class because Flow and Autoplay depend on it.

Use generic playback/session naming:

- `playback_sessions`
- `queue_items`
- `playback_events`

Decision:

- Use generic playback session tables.

Reason:

- Flow, autoplay, album playback, playlist playback, and manual queues all share
  common queue/event behavior.

Suggested generic fields:

`playback_sessions`

- `id`
- `user_id`
- `session_type`: flow, autoplay, album, playlist, manual_queue, search
- `source_type`
- `source_id`
- `status`
- `settings_json`
- `state_json`
- `started_at`
- `updated_at`
- `ended_at`

`queue_items`

- `id`
- `session_id`
- `track_id`
- `position`
- `state`: queued, visible, playing, played, skipped, removed
- `source_type`
- `source_id`
- `generation`
- `score`
- `score_breakdown_json`
- `reason_json`
- `created_at`
- `played_at`

`playback_events`

- raw event log as described in the Flow plan.

## User Preference And Feedback

Keep explicit and implicit signals separate.

Explicit:

- liked track;
- disliked track;
- liked artist/release if supported;
- saved playlist/mix;
- manual rating if added.

Implicit:

- completed play;
- early skip;
- late skip;
- replay;
- repeated listens;
- queue removal;
- session abandonment.

Recommended tables:

- `user_track_preferences`
- `user_artist_preferences`
- `user_release_preferences`
- `playback_events`

Track/release/artist preference tables should store current aggregate state.
Raw events remain the source of truth.

## Embeddings And Vector Storage

Do not overload the main metadata tables with large vector blobs.

Recommended storage:

- main app database for metadata, sessions, preferences;
- embeddings database for current Discogs-EffNet global/segment embeddings;
- separate MAEST embeddings database because it is heavy;
- HNSW index files per model/strategy.

Entities:

- `track_embeddings`
- `track_embedding_segments`
- `release_embeddings`
- `artist_embeddings` optional later
- `embedding_indexes` metadata table optional

Release embeddings:

- derived from track embeddings;
- store centroid and medoid;
- store aggregate summaries separately.

Artist embeddings:

- optional derived aggregate;
- useful for similar artists but should not replace track/release evidence.

## Album Aggregates

Album/release recommendation needs precomputed aggregates.

Recommended tables:

`release_aggregates`

- `release_id`
- `model_name`
- `centroid_blob` or reference
- `medoid_track_id`
- `track_count`
- `duration`
- `audio_summary_json`
- `head_label_summary_json`
- `region_match_summary_json`
- `updated_at`

`release_user_stats`

- `release_id`
- `user_id`
- `play_count`
- `completed_track_count`
- `skip_count`
- `liked_track_count`
- `last_played_at`
- `last_completed_at`
- `saved_at`

## Artist Aggregates

Artist pages and similar artists need aggregate data.

Recommended tables:

`artist_aggregates`

- `artist_id`
- `track_count`
- `release_count`
- `latest_release_id`
- `top_tracks_json` or computed query/cache
- `similar_artists_json` or separate table
- `updated_at`

`artist_user_stats`

- `artist_id`
- `user_id`
- `play_count`
- `completed_count`
- `skip_count`
- `liked_track_count`
- `last_played_at`

## Dashboard Caches

Dashboard shelves can be computed dynamically or cached.

Options:

1. Compute every request.
2. Cache shelf results with invalidation.
3. Store generated shelf snapshots.

Recommendation:

- Simple shelves can query live.
- Expensive recommendation shelves should cache snapshots.

Potential table:

`dashboard_shelf_cache`

- `user_id`
- `shelf_key`
- `settings_hash`
- `items_json`
- `generated_at`
- `expires_at`

## API Design Notes

The backend should expose clean entity APIs for future web clients.

Candidate endpoints:

- `GET /api/v1/search?q=...`
- `GET /api/v1/artists/{id}`
- `GET /api/v1/artists/{id}/discography`
- `GET /api/v1/artists/{id}/top-tracks`
- `GET /api/v1/artists/{id}/similar`
- `GET /api/v1/releases/{id}`
- `GET /api/v1/releases/{id}/tracks`
- `GET /api/v1/releases/{id}/related-discography`
- `GET /api/v1/releases/{id}/recommendations`
- `GET /api/v1/playlists/{id}` later
- `POST /api/v1/playback/sessions`
- `POST /api/v1/playback/events`
- `GET /api/v1/dashboard`
- `GET /api/v1/dashboard/shelves/{key}`
- `GET /api/v1/mixes`
- `POST /api/v1/mixes/{id}/save`

API response shape should be stable and UI-friendly:

- core entity data;
- display metadata;
- cover/artwork URLs;
- action availability;
- optional debug/reason fields in advanced mode.

## Finalized Choices

### Artist/release metadata source

Decision:

- Start with local tags + Navidrome raw metadata.
- Keep external ID mapping ready for future providers.

### Release type inference

Decision:

- Do not infer release type in product behavior.
- Use explicit provider/tag metadata only.
- If type is missing, store `unknown` and show it as `Releases`.
- Future inference can exist only as debug/analysis metadata.

### Generic events vs feature-specific events

Decision:

- Generic playback event table.

Reason:

- Future web can use the same event stream for Flow, autoplay, albums,
  playlists, search, dashboard, and quality metrics.
